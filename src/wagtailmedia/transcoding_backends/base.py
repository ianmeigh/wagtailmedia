from abc import ABC, abstractmethod


class TranscodingError(Exception):
    """Base exception for transcoding operations."""

    pass


class AbstractTranscodingBackend(ABC):
    @abstractmethod
    def start_transcode(self, media_file, target_format):
        pass

    @abstractmethod
    def stop_transcode(self, task_id):
        pass
