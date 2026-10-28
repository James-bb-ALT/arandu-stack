use std::io::{Read, Write};
use std::os::unix::net::UnixStream;
use std::time::Instant;

use crate::{Result, ServerError, ServerErrorCode};

pub const MAX_FRAME_BYTES: usize = 131_072;
const HEADER_BYTES: usize = 4;

pub fn read_frame(reader: &mut impl Read) -> Result<Vec<u8>> {
    let mut header = [0_u8; HEADER_BYTES];
    reader
        .read_exact(&mut header)
        .map_err(|_| ServerError::new(ServerErrorCode::FrameIo))?;
    let length = usize::try_from(u32::from_be_bytes(header))
        .map_err(|_| ServerError::new(ServerErrorCode::FrameTooLarge))?;
    if length == 0 {
        return Err(ServerError::new(ServerErrorCode::EmptyFrame));
    }
    if length > MAX_FRAME_BYTES {
        return Err(ServerError::new(ServerErrorCode::FrameTooLarge));
    }
    let mut payload = vec![0_u8; length];
    reader
        .read_exact(&mut payload)
        .map_err(|_| ServerError::new(ServerErrorCode::FrameIo))?;
    Ok(payload)
}

pub fn write_frame(writer: &mut impl Write, payload: &[u8]) -> Result<()> {
    if payload.is_empty() {
        return Err(ServerError::new(ServerErrorCode::EmptyFrame));
    }
    if payload.len() > MAX_FRAME_BYTES {
        return Err(ServerError::new(ServerErrorCode::ResponseTooLarge));
    }
    let length = u32::try_from(payload.len())
        .map_err(|_| ServerError::new(ServerErrorCode::ResponseTooLarge))?;
    writer
        .write_all(&length.to_be_bytes())
        .and_then(|()| writer.write_all(payload))
        .and_then(|()| writer.flush())
        .map_err(|_| ServerError::new(ServerErrorCode::FrameIo))
}

pub(crate) fn read_frame_until(stream: &mut UnixStream, deadline: Instant) -> Result<Vec<u8>> {
    let mut header = [0_u8; HEADER_BYTES];
    read_exact_until(stream, &mut header, deadline)?;
    let length = usize::try_from(u32::from_be_bytes(header))
        .map_err(|_| ServerError::new(ServerErrorCode::FrameTooLarge))?;
    if length == 0 {
        return Err(ServerError::new(ServerErrorCode::EmptyFrame));
    }
    if length > MAX_FRAME_BYTES {
        return Err(ServerError::new(ServerErrorCode::FrameTooLarge));
    }
    let mut payload = vec![0_u8; length];
    read_exact_until(stream, &mut payload, deadline)?;
    Ok(payload)
}

pub(crate) fn write_frame_until(
    stream: &mut UnixStream,
    payload: &[u8],
    deadline: Instant,
) -> Result<()> {
    if payload.is_empty() {
        return Err(ServerError::new(ServerErrorCode::EmptyFrame));
    }
    if payload.len() > MAX_FRAME_BYTES {
        return Err(ServerError::new(ServerErrorCode::ResponseTooLarge));
    }
    let length = u32::try_from(payload.len())
        .map_err(|_| ServerError::new(ServerErrorCode::ResponseTooLarge))?;
    write_all_until(stream, &length.to_be_bytes(), deadline)?;
    write_all_until(stream, payload, deadline)?;
    stream
        .set_write_timeout(Some(remaining(deadline)?))
        .and_then(|()| stream.flush())
        .map_err(map_deadline_io)?;
    ensure_before(deadline)
}

fn read_exact_until(
    stream: &mut UnixStream,
    mut destination: &mut [u8],
    deadline: Instant,
) -> Result<()> {
    while !destination.is_empty() {
        stream
            .set_read_timeout(Some(remaining(deadline)?))
            .map_err(|_| ServerError::new(ServerErrorCode::SocketConfiguration))?;
        match stream.read(destination) {
            Ok(0) => return Err(ServerError::new(ServerErrorCode::FrameIo)),
            Ok(count) => {
                destination = &mut destination[count..];
                ensure_before(deadline)?;
            }
            Err(error) if error.kind() == std::io::ErrorKind::Interrupted => {}
            Err(error) => return Err(map_deadline_io(error)),
        }
    }
    Ok(())
}

fn write_all_until(stream: &mut UnixStream, mut source: &[u8], deadline: Instant) -> Result<()> {
    while !source.is_empty() {
        stream
            .set_write_timeout(Some(remaining(deadline)?))
            .map_err(|_| ServerError::new(ServerErrorCode::SocketConfiguration))?;
        match stream.write(source) {
            Ok(0) => return Err(ServerError::new(ServerErrorCode::FrameIo)),
            Ok(count) => {
                source = &source[count..];
                ensure_before(deadline)?;
            }
            Err(error) if error.kind() == std::io::ErrorKind::Interrupted => {}
            Err(error) => return Err(map_deadline_io(error)),
        }
    }
    Ok(())
}

fn remaining(deadline: Instant) -> Result<std::time::Duration> {
    deadline
        .checked_duration_since(Instant::now())
        .filter(|remaining| !remaining.is_zero())
        .ok_or_else(|| ServerError::new(ServerErrorCode::RequestTimeout))
}

fn ensure_before(deadline: Instant) -> Result<()> {
    if Instant::now() < deadline {
        Ok(())
    } else {
        Err(ServerError::new(ServerErrorCode::RequestTimeout))
    }
}

fn map_deadline_io(error: std::io::Error) -> ServerError {
    if matches!(
        error.kind(),
        std::io::ErrorKind::TimedOut | std::io::ErrorKind::WouldBlock
    ) {
        ServerError::new(ServerErrorCode::RequestTimeout)
    } else {
        ServerError::new(ServerErrorCode::FrameIo)
    }
}

#[cfg(test)]
mod tests {
    use std::io::Cursor;

    use super::*;

    #[test]
    fn fragmented_reads_and_coalesced_frames_are_bounded() {
        struct OneByteReader(Cursor<Vec<u8>>);

        impl Read for OneByteReader {
            fn read(&mut self, buffer: &mut [u8]) -> std::io::Result<usize> {
                let limit = buffer.len().min(1);
                self.0.read(&mut buffer[..limit])
            }
        }

        let payload = b"FIXTURE_TECNICA_A";
        let mut bytes = Vec::new();
        write_frame(&mut bytes, payload).expect("first");
        write_frame(&mut bytes, b"FIXTURE_TECNICA_B").expect("second");
        let mut reader = OneByteReader(Cursor::new(bytes));
        assert_eq!(read_frame(&mut reader), Ok(payload.to_vec()));
        assert_eq!(read_frame(&mut reader), Ok(b"FIXTURE_TECNICA_B".to_vec()));
    }

    #[test]
    fn frame_boundaries_reject_before_payload_allocation() {
        let exact = vec![b'A'; MAX_FRAME_BYTES];
        let mut encoded = Vec::new();
        write_frame(&mut encoded, &exact).expect("exact frame");
        assert_eq!(read_frame(&mut encoded.as_slice()), Ok(exact));

        let one_over_header = u32::try_from(MAX_FRAME_BYTES + 1)
            .expect("limit fits")
            .to_be_bytes();
        assert_eq!(
            read_frame(&mut one_over_header.as_slice())
                .expect_err("length rejected before body read")
                .code(),
            ServerErrorCode::FrameTooLarge
        );
        assert_eq!(
            read_frame(&mut [0_u8; 4].as_slice())
                .expect_err("empty frame")
                .code(),
            ServerErrorCode::EmptyFrame
        );
        assert_eq!(
            write_frame(&mut Vec::new(), &vec![0_u8; MAX_FRAME_BYTES + 1])
                .expect_err("oversized response")
                .code(),
            ServerErrorCode::ResponseTooLarge
        );
    }

    #[test]
    fn truncated_header_and_payload_fail_closed() {
        assert_eq!(
            read_frame(&mut [0_u8; 3].as_slice())
                .expect_err("truncated header")
                .code(),
            ServerErrorCode::FrameIo
        );
        let mut truncated = 2_u32.to_be_bytes().to_vec();
        truncated.push(b'A');
        assert_eq!(
            read_frame(&mut truncated.as_slice())
                .expect_err("truncated body")
                .code(),
            ServerErrorCode::FrameIo
        );
    }
}
