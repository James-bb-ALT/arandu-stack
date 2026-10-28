fn main() {
    match plan_eval::evaluate_cli(std::env::args_os()) {
        Ok(output) => print!("{}", String::from_utf8_lossy(&output)),
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(2);
        }
    }
}
