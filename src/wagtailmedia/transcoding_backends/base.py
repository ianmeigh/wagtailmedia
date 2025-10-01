from abc import ABC, abstractmethod


class BaseTranscodingBackend(ABC):
    @abstractmethod
    def start_transcode(self, media_file, target_format):
        pass

    @abstractmethod
    def stop_transcode():
        pass
