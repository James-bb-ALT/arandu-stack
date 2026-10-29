use std::{env, net::TcpListener, process::ExitCode};

fn main() -> ExitCode {
    let mut arguments = env::args().skip(1);
    let command = arguments.next();
    let flag = arguments.next();
    let listen = arguments.next();
    if command.as_deref() != Some("serve")
        || flag.as_deref() != Some("--listen")
        || listen.is_none()
        || arguments.next().is_some()
    {
        eprintln!("usage: local-nlu serve --listen ADDRESS:PORT");
        return ExitCode::from(64);
    }
    let listen = listen.expect("checked");
    let listener = match TcpListener::bind(&listen) {
        Ok(listener) => listener,
        Err(error) => {
            eprintln!("failed to bind local NLU listener: {error}");
            return ExitCode::from(70);
        }
    };
    if let Err(error) = local_nlu::server::serve(&listener) {
        eprintln!("local NLU server failed: {error}");
        return ExitCode::from(70);
    }
    ExitCode::SUCCESS
}
