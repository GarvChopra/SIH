"""Service-layer errors. The web layer maps these to HTTP responses."""


class ServiceError(Exception):
    status_code = 400


class ValidationError(ServiceError):
    status_code = 400


class NotFound(ServiceError):
    status_code = 404


class PermissionDenied(ServiceError):
    status_code = 403
