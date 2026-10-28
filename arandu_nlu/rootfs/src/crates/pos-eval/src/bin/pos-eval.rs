#![forbid(unsafe_code)]

use std::{
    io::{self, Write as _},
    process::ExitCode,
};

fn main() -> ExitCode {
    let output = match pos_eval::evaluate_cli(std::env::args_os()) {
        Ok(output) => output,
        Err(error) => {
            eprintln!("pos-eval: {error}");
            return ExitCode::FAILURE;
        }
    };
    if io::stdout().lock().write_all(&output).is_err() {
        eprintln!("pos-eval: failed to write report");
        return ExitCode::FAILURE;
    }
    ExitCode::SUCCESS
}
