import logging

class SpecialFilter(logging.Filter):
    def __init__(self, foo=None):
        super().__init__()
        self.foo = foo

    def filter(self, record):
        # Add extra context to the log record
        record.foo = self.foo
        return True  # Allow all records to pass
