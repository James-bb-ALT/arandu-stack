#![forbid(unsafe_code)]

fn main() {
    match nlu_data::cli::run(std::env::args_os().skip(1)) {
        Ok(message) => println!("{message}"),
        Err(error) => {
            eprintln!("NLU_DATA_FAIL: {error}");
            std::process::exit(2);
        }
    }
}
