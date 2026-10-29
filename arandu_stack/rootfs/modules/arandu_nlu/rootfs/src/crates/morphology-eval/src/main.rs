#![forbid(unsafe_code)]

use std::{
    io::{self, Write as _},
    process::ExitCode,
};

fn main() -> ExitCode {
    let output = match morphology_eval::evaluate_cli(std::env::args_os()) {
        Ok(output) => output,
        Err(error) => {
            eprintln!("morphology-eval: {error}");
            return ExitCode::FAILURE;
        }
    };
    if io::stdout().lock().write_all(&output).is_err() {
        eprintln!("morphology-eval: failed to write report");
        return ExitCode::FAILURE;
    }
    ExitCode::SUCCESS
}
