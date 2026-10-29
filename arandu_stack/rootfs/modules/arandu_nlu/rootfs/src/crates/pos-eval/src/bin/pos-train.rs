#![forbid(unsafe_code)]

use std::{
    io::{self, Write as _},
    process::ExitCode,
};

fn main() -> ExitCode {
    let output = match pos_eval::train_cli(std::env::args_os()) {
        Ok(output) => output,
        Err(error) => {
            eprintln!("pos-train: {error}");
            return ExitCode::FAILURE;
        }
    };
    if io::stdout().lock().write_all(&output).is_err() {
        eprintln!("pos-train: failed to write summary");
        return ExitCode::FAILURE;
    }
    ExitCode::SUCCESS
}
