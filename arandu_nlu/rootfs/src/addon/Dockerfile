FROM docker.io/library/rust:1.98.0-alpine3.22@sha256:2e452153b2dc6bed8ef123c4801cfd3f637e7c092846f974ecdce5e64af3120c AS builder

WORKDIR /build
ENV CARGO_PROFILE_RELEASE_STRIP=none
COPY .cargo .cargo
COPY engine engine
COPY vendor vendor
RUN cargo build --manifest-path engine/Cargo.toml --locked --offline --release

FROM scratch

ARG BUILD_ARCH
ARG BUILD_VERSION
LABEL \
    io.hass.arch="${BUILD_ARCH}" \
    io.hass.type="app" \
    io.hass.version="${BUILD_VERSION}"

COPY --from=builder /build/engine/target/release/local-nlu /usr/bin/local-nlu
COPY LICENSE /licenses/Apache-2.0.txt
COPY THIRD_PARTY_NOTICES.md /licenses/THIRD_PARTY_NOTICES.md
COPY engine/data/STT-MIT.txt /licenses/STT-MIT.txt
COPY container-inputs.json /licenses/container-inputs.json
COPY vendor/memchr-2.8.3/LICENSE-MIT /licenses/memchr-MIT.txt
COPY vendor/unicode-ident-1.0.24/LICENSE-UNICODE /licenses/unicode-ident-Unicode-3.0.txt
COPY licenses /licenses/runtime

USER 65534:65534
EXPOSE 11555
CMD ["/usr/bin/local-nlu", "serve", "--listen", "0.0.0.0:11555"]
