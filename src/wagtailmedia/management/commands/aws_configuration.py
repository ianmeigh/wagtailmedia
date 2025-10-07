import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = (
        "Configure AWS resources (SQS queue, EventBridge rule) and utilities for "
        "MediaConvert integration. This should be run after following the AWS "
        "policy setup instructions in the documentation."
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Check boto3 package installed and import
        try:
            import boto3
            import botocore.exceptions as botocore_exceptions

            self.boto3 = boto3
            self.botocore_exceptions = botocore_exceptions
        except ImportError as err:
            raise CommandError(
                "boto3 is required for AWS setup. Please install it via pip."
            ) from err

        # Check AWS custom settings or use defaults
        self.sqs_queue_name = (
            getattr(settings, "AWS_SQS_QUEUE_NAME", None) or "mediaconvert-messages"
        )

        self.eventbridge_rule_name = (
            getattr(settings, "AWS_EVENTBRIDGE_RULE_NAME", None)
            or "mediaconvert-job-events"
        )

    def create_aws_service_client(self, *, service_identifier):
        """Create a boto3 client for the specified AWS service."""
        try:
            client = self.boto3.client(service_identifier)
        except self.botocore_exceptions.PartialCredentialsError as err:
            raise CommandError(err) from err
        except self.botocore_exceptions.NoRegionError as err:
            raise CommandError("AWS region not specified.") from err
        return client

    def get_or_create_sqs_queue(
        self, *, sqs_client, name: str, attributes: dict | None = None
    ) -> tuple[str, str]:
        """Get or create an SQS queue by name. Queue creation is idempotent if the
        parameters provided are the same.

        Args:
            sqs_client (botocore.client.BaseClient): The SQS client.
            name (str): The name of the SQS queue.
            attributes (dict, optional): Attributes to set on the SQS queue. Defaults
              to None which will enable long polling.
              See https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-short-and-long-polling.html.

        Returns:
            tuple[str, str]: The URL and ARN of the SQS queue.
        """

        if attributes is None:
            attributes = {
                "ReceiveMessageWaitTimeSeconds": "20",  # Enable long polling
            }

        try:
            queue_url = sqs_client.create_queue(QueueName=name, Attributes=attributes)[
                "QueueUrl"
            ]
        except self.botocore_exceptions.NoCredentialsError as err:
            raise CommandError("AWS credentials not found.") from err
        except sqs_client.exceptions.QueueDeletedRecently as err:
            raise CommandError(
                "Queue was recently deleted. Please try again after in one minute."
            ) from err
        except sqs_client.exceptions.QueueNameExists:
            queue_url = sqs_client.get_queue_url(QueueName=name)["QueueUrl"]
        except self.botocore_exceptions.ClientError as err:
            raise CommandError(err) from err

        attrs = sqs_client.get_queue_attributes(
            QueueUrl=queue_url, AttributeNames=["QueueArn"]
        )

        return queue_url, attrs["Attributes"]["QueueArn"]

    def get_or_create_eventbridge_rule(
        self, *, events_client, name: str, pattern: dict
    ) -> str:
        """Get or create an EventBridge rule by name.

        Checks if an EventBridge rule with the given name exists, and creates it if not.

        Args:
            events_client ((botocore.client.BaseClient)): The EventBridge client.
            name (str): The name of the EventBridge rule.
            pattern (dict): The event pattern for the rule.

        Returns:
            str: The ARN of the EventBridge rule.
        """

        rules = events_client.list_rules(NamePrefix=name).get("Rules", [])
        for rule in rules:
            if rule["Name"] == name:
                return rule["Arn"]
        # If not, create it
        rule_response = events_client.put_rule(
            Name=name,
            EventPattern=json.dumps(pattern),
            State="ENABLED",
        )
        return rule_response["RuleArn"]

    def handle(self, *args, **options):
        # Create AWS service clients
        self.stdout.write("Creating AWS clients...")
        sqs_client = self.create_aws_service_client(service_identifier="sqs")
        events_client = self.create_aws_service_client(service_identifier="events")

        # Create SQS queue
        self.stdout.write(f"Creating or getting SQS queue '{self.sqs_queue_name}'...")
        queue_url, queue_arn = self.get_or_create_sqs_queue(
            sqs_client=sqs_client, name=self.sqs_queue_name
        )
        self.stdout.write(f"SQS queue URL: {queue_url}, ARN: {queue_arn}")

        # EventBridge pattern to specify which events to send to a target
        # https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-event-patterns.html
        pattern = {
            "source": ["aws.mediaconvert"],
            "detail-type": ["MediaConvert Job State Change"],
            "detail": {"status": ["PROGRESSING", "COMPLETE", "ERROR"]},
        }

        # Create EventBridge rule to capture MediaConvert job state change events
        self.stdout.write(
            f"Creating or getting EventBridge rule '{self.eventbridge_rule_name}'..."
        )
        rule_arn = self.get_or_create_eventbridge_rule(
            events_client=events_client,
            name=self.eventbridge_rule_name,
            pattern=pattern,
        )
        self.stdout.write(f"EventBridge rule ARN: {rule_arn}")

        # Add the queue as a target to the rule, otherwise events won't flow.
        self.stdout.write("Adding SQS queue as target to EventBridge rule...")
        events_client.put_targets(
            Rule=self.eventbridge_rule_name,
            Targets=[{"Id": "mediaconvert-sqs-target", "Arn": queue_arn}],
        )

        #  Allow EventBridge to publish to SQS (queue resource policy)
        sqs_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "AllowEventBridgeToSend",
                    "Effect": "Allow",
                    "Principal": {"Service": "events.amazonaws.com"},
                    "Action": "sqs:SendMessage",
                    "Resource": queue_arn,
                    "Condition": {"ArnEquals": {"aws:SourceArn": rule_arn}},
                }
            ],
        }

        self.stdout.write("Setting SQS queue policy to allow EventBridge access...")
        sqs_client.set_queue_attributes(
            QueueUrl=queue_url, Attributes={"Policy": json.dumps(sqs_policy)}
        )

        self.stdout.write(
            self.style.SUCCESS(
                "AWS configuration complete. Please ensure the remaining AWS policies "
                "are set up as per the documentation before attempting to transcode "
                "media."
            )
        )
