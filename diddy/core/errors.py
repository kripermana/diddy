"""Exception yang diterjemahkan menjadi respons JSON {"error": ...}."""


class ApiError(Exception):
    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.status = status
