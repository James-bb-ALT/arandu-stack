# Developer-only environment. The distributable add-on still uses addon/Dockerfile.
FROM docker.io/library/rust:1.98.0-alpine3.22@sha256:2e452153b2dc6bed8ef123c4801cfd3f637e7c092846f974ecdce5e64af3120c

RUN apk add --no-cache git ruby=3.4.4-r0 ruby-yaml=0.4.0-r1 python3=3.12.15-r0 tzdata=2026e-r0 \
    && ruby -rjson -ryaml -e 'puts "Ruby JSON/YAML ready"' \
    && rustup component add clippy rustfmt --toolchain 1.98.0-x86_64-unknown-linux-musl

# Select the installed Linux toolchain, without downloading the original Mac target.
ENV RUSTUP_TOOLCHAIN=1.98.0-x86_64-unknown-linux-musl \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /workspace
