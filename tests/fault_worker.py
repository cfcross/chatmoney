"""Child process used solely by the crash/recovery integration test."""

import os
from app.channels import Sender
from app.worker import Worker


def crash():
    os._exit(87)


if __name__ == "__main__":
    Worker(["support"], sender=Sender(after_accept=crash)).tick()
