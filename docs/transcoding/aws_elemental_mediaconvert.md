
# AWS Elemental MediaConvert Transcoding Backend

## Installation

This backend is based on the boto3 library which must be installed in your host project; the minimum required version is 1.40.43 although we always recommend using the most recent release. Either add it to your host project's requirements or use the optional `boto3` extra e.g:

```bash
pip install wagtailmedia[boto3]
```

## Host Application Settings

You can use a number of methods to specify [credentials for boto3](https://boto3.amazonaws.com/v1/documentation/api/latest/guide/configuration.html). We suggest you stick with environment variables. To do that, you can to set the following variables:

- AWS_ACCESS_KEY_ID
- AWS_SECRET_ACCESS_KEY
- AWS_DEFAULT_REGION

Optionally you can provide custom names for the Simple Queue Services queue and EventBridge rule. The following code allows configuration via environment variables.

```python
AWS_SQS_QUEUE_NAME = os.environ.get("AWS_SQS_QUEUE_NAME", "")  # default: "mediaconvert-messages"
AWS_EVENTBRIDGE_RULE_NAME = os.environ.get("AWS_EVENTBRIDGE_RULE_NAME", "")  # default: "mediaconvert-job-events"
```

## AWS Permissions (Partially) Automated Setup

This guide explains how to configure AWS IAM roles and policies for secure, automated use of AWS Elemental MediaConvert as a transcoding backend.

---

## Prerequisites

- An AWS account with permissions to create IAM roles, policies, and MediaConvert jobs
- Access to the AWS Console or CLI
- An S3 bucket for input/output media

---

## 1. Create the MediaConvert Service Role

MediaConvert requires a service role with permissions to read from and write to your S3 bucket. This role is assumed by the MediaConvert service when running jobs.

1. **Create a new IAM policy** (e.g., `MediaConvert_Default_Role_Policy`) with the following permissions:

    ```json
    {
      "Version": "2012-10-17",
      "Statement": [
        {
          "Effect": "Allow",
          "Action": [
            "s3:Get*",
            "s3:List*"
          ],
          "Resource": [
            "arn:aws:s3:::YOUR_BUCKET_NAME/*"
          ]
        },
        {
          "Effect": "Allow",
          "Action": [
            "s3:Put*"
          ],
          "Resource": [
            "arn:aws:s3:::YOUR_BUCKET_NAME/*"
          ]
        }
      ]
    }
    ```

2. **Create a new IAM role for MediaConvert**:
    - In the AWS Console, go to IAM > Roles > Create role
    - Select **AWS service** and choose **MediaConvert**
    - Proceed with defaults
    - Name the role (e.g., `MediaConvert_Default_Role`)
    - Edit the role and remove the default policies
    - Attach the MediaConvert IAM policy you created in step 1
    - The Trusted entities (under the Trust relationships tab) should be as below:

      ```json
      {
        "Version": "2012-10-17",
        "Statement": [
          {
            "Effect": "Allow",
            "Principal": { "Service": "mediaconvert.amazonaws.com" },
            "Action": "sts:AssumeRole"
          }
        ]
      }
      ```

---

## 2. IAM Permissions for Normal Operation

These permissions are required for the IAM user, group, or role that will submit MediaConvert jobs and query their status.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowPassMediaConvertRoleToService",
      "Effect": "Allow",
      "Action": "iam:PassRole",
      "Resource": "arn:aws:iam::YOUR_AWS_ACCOUNT_ID:role/service-role/MediaConvert_Default_Role",
      "Condition": {
        "StringEquals": {
          "iam:PassedToService": "mediaconvert.amazonaws.com"
        }
      }
    },
    {
      "Sid": "AllowMediaConvertJobAndQueueManagement",
      "Effect": "Allow",
      "Action": [
        "mediaconvert:GetQueue",
        "mediaconvert:CreateJob"
      ],
      "Resource": "arn:aws:mediaconvert:YOUR_AWS_REGION:YOUR_AWS_ACCOUNT_ID:queues/Default"
    }
  ]
}
```

---

## 3. IAM Permissions for Management Command (Setup Automation)

If you use the provided management command to automate some of the AWS resource setup (SQS, EventBridge, etc.), grant these permissions to the user or role running the command (as specified with the AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY settings):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowMediaConvertRuleAndTargetManagement",
      "Effect": "Allow",
      "Action": [
        "events:PutTargets",
        "events:PutRule",
        "events:ListRules"
      ],
      "Resource": "*"
    },
    {
      "Sid": "AllowSQSRuleCreation",
      "Effect": "Allow",
      "Action": [
        "sqs:GetQueueAttributes",
        "sqs:CreateQueue",
        "sqs:SetQueueAttributes"
      ],
      "Resource": "*"
    }
  ]
}
```

These permissions are just required to run the setup management command and can be removed afterwards.

---

## 4. Additional Notes

- Always use the full S3 ARN (e.g., `arn:aws:s3:::YOUR_BUCKET_NAME/*`) in policies, not S3 URLs.
- The `iam:PassRole` permission is required for the user or automation that submits jobs to MediaConvert.
- The MediaConvert service role must have a trust policy allowing `mediaconvert.amazonaws.com` to assume it.
