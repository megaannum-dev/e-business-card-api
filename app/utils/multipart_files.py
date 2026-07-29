from starlette.datastructures import UploadFile


def optional_upload_file(file: UploadFile | str | None) -> UploadFile | None:
    """Swagger UI may send an empty string for omitted optional file fields."""
    if isinstance(file, UploadFile) and file.filename:
        return file
    return None
