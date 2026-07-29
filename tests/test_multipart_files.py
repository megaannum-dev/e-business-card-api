import unittest

from starlette.datastructures import Headers, UploadFile

from app.utils.multipart_files import optional_upload_file


class MultipartFilesTests(unittest.TestCase):
    def test_accepts_starlette_upload_file(self) -> None:
        upload = UploadFile(
            filename="card.jpg",
            file=object(),
            headers=Headers({"content-type": "image/jpeg"}),
        )
        self.assertIs(optional_upload_file(upload), upload)

    def test_rejects_empty_string_from_swagger(self) -> None:
        self.assertIsNone(optional_upload_file(""))

    def test_rejects_upload_without_filename(self) -> None:
        upload = UploadFile(filename="", file=object())
        self.assertIsNone(optional_upload_file(upload))


if __name__ == "__main__":
    unittest.main()
