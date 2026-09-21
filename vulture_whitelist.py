# Vulture whitelist - intentional false positives.

# Pydantic field validator class methods must use cls for ruff's classmethod
# naming rule, but these validators only need the value parameter.
_ = cls  # noqa: F821

# aioboto3 S3 methods expose boto-style keyword names. Test-only protocols keep
# those names so calls match the real client surface.
_ = Bucket  # noqa: F821
_ = ClientMethod  # noqa: F821
_ = ExpiresIn  # noqa: F821
_ = Key  # noqa: F821
_ = MultipartUpload  # noqa: F821
_ = Params  # noqa: F821
_ = UploadId  # noqa: F821
