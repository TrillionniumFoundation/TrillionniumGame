#![forbid(unsafe_code)]

use std::path::PathBuf;

fn main() {
    println!("cargo:rerun-if-changed=proto/nakama-healthcheck.proto");

    let protoc = protoc_bin_vendored::protoc_bin_path()
        .expect("the reviewed vendored protoc package must provide this target binary");
    let protobuf_include = protoc_bin_vendored::include_path()
        .expect("the reviewed vendored protoc package must provide well-known types");
    std::env::set_var("PROTOC", protoc);

    let includes = [PathBuf::from("proto"), protobuf_include];
    tonic_prost_build::configure()
        .build_client(true)
        .build_server(true)
        .compile_well_known_types(true)
        .compile_protos(&["proto/nakama-healthcheck.proto"], &includes)
        .expect("the pinned Nakama Healthcheck protobuf subset must compile");
}
