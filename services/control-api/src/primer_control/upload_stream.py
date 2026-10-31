"""An upload's bytes, read off the request as they arrive.

FastAPI's `UploadFile` is the obvious way to take a file, and it costs every
byte a second write: Starlette parses the whole form into a spooled
temporary file before the endpoint runs, and only then can the endpoint copy
it into the source store. Past a megabyte that spool is on local disk, so a
large upload fills the pod's scratch space with a copy nobody keeps, and the
quota cannot stop it early because the store has not seen a byte yet.

This parses the same `multipart/form-data` body incrementally and hands the
file part's data to the store as it is decoded. The request contract is
unchanged - one part named `file`, carrying a filename - so the browser and
the web server send exactly what they did.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fastapi import Request, status
from primer_contracts.errors import ErrorCode
from primer_service.errors import ProblemError
from python_multipart import MultipartParser
from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import parse_options_header

FIELD = "file"

#: The request body as OpenAPI describes it. Declared by hand because the
#: endpoints no longer take an `UploadFile` parameter for FastAPI to read it
#: from; the shape is the one that parameter produced.
REQUEST_BODY: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": [FIELD],
                    "properties": {
                        FIELD: {
                            "type": "string",
                            "contentMediaType": "application/octet-stream",
                            "description": "The source file to ingest",
                        }
                    },
                }
            }
        },
    }
}


def malformed(detail: str) -> ProblemError:
    return ProblemError(
        code=ErrorCode.VALIDATION_FAILED,
        title="Upload malformed",
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=detail,
    )


class UploadStream:
    """The `file` part of one multipart request, decoded as the body arrives.

    Every other part is read past and dropped, so nothing but the file is
    held. Only the first `file` part counts; a second is ignored rather than
    concatenated onto the first.
    """

    def __init__(self, body: AsyncIterator[bytes], boundary: bytes, *, chunk_bytes: int) -> None:
        self._body = body
        self._chunk_bytes = chunk_bytes
        self._pending: list[bytes] = []
        self._pending_bytes = 0
        self._header_name = b""
        self._header_value = b""
        self._disposition = b""
        self._in_file = False
        self._file_ended = False
        self.filename: str | None = None
        self._parser = MultipartParser(
            boundary,
            {
                "on_part_begin": self._part_begin,
                "on_header_field": self._header_field,
                "on_header_value": self._header_value_data,
                "on_header_end": self._header_end,
                "on_headers_finished": self._headers_finished,
                "on_part_data": self._part_data,
                "on_part_end": self._part_end,
            },
        )

    def _part_begin(self) -> None:
        self._disposition = b""
        self._in_file = False

    def _header_field(self, data: bytes, start: int, end: int) -> None:
        self._header_name += data[start:end]

    def _header_value_data(self, data: bytes, start: int, end: int) -> None:
        self._header_value += data[start:end]

    def _header_end(self) -> None:
        if self._header_name.lower() == b"content-disposition":
            self._disposition = self._header_value
        self._header_name = b""
        self._header_value = b""

    def _headers_finished(self) -> None:
        _, options = parse_options_header(self._disposition)
        if self.filename is None and options.get(b"name") == FIELD.encode():
            # A `file` part without a filename is a form field, not a file;
            # an empty string is kept so the caller's name check refuses it.
            self.filename = options.get(b"filename", b"").decode("utf-8", "replace")
            self._in_file = True

    def _part_data(self, data: bytes, start: int, end: int) -> None:
        if self._in_file:
            self._pending.append(data[start:end])
            self._pending_bytes += end - start

    def _part_end(self) -> None:
        if self._in_file:
            self._in_file = False
            self._file_ended = True

    async def _feed(self) -> bool:
        """Decode one more piece of the body; False once there is none."""
        chunk = await anext(self._body, None)
        try:
            if chunk is None:
                self._parser.finalize()
                return False
            self._parser.write(chunk)
        except MultipartParseError as error:
            raise malformed("The upload is not valid multipart/form-data.") from error
        return True

    async def start(self) -> str:
        """Read up to the file part's headers and return its filename."""
        while self.filename is None and await self._feed():
            pass
        if self.filename is None:
            raise malformed(f"The upload must carry a file in a part named {FIELD!r}.")
        return self.filename

    async def chunks(self) -> AsyncIterator[bytes]:
        """The file's bytes, in pieces of about `chunk_bytes`.

        Coalesced rather than passed through at the server's read size,
        because the store writes each piece on a worker thread and a
        sixty-four kilobyte hop per write is most of the cost of a fast disk.
        """
        while True:
            more = self._file_ended or await self._feed()
            last = self._file_ended or not more
            if self._pending and (last or self._pending_bytes >= self._chunk_bytes):
                yield b"".join(self._pending)
                self._pending.clear()
                self._pending_bytes = 0
            if self._file_ended:
                return
            if not more:
                raise malformed("The upload ended before the file did.")


def open_upload(request: Request, *, chunk_bytes: int) -> UploadStream:
    """Begin reading the request's file part without reading the body yet."""
    media_type, options = parse_options_header(request.headers.get("content-type", ""))
    boundary = options.get(b"boundary")
    if media_type != b"multipart/form-data" or not boundary:
        raise malformed(f"Upload the file as multipart/form-data, in a part named {FIELD!r}.")
    return UploadStream(request.stream(), boundary, chunk_bytes=chunk_bytes)
