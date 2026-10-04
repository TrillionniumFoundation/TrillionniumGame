#![forbid(unsafe_code)]

use std::path::PathBuf;

fn main() {
    println!("cargo:rerun-if-changed=proto/nakama-healthcheck.proto");

    let protoc = protoc_bin_vendored::protoc_bin_path()
        .expect("the reviewed vendored protoc package must provide this target binary");
    let protobuf_include = protoc_bin_vendored::include_path()
        .expect("the reviewed vendored protoc package must provide well-known types");
    std::env::set_var("PROTOC", protoc);

    let protos = [PathBuf::from("proto/nakama-healthcheck.proto")];
    let includes = [PathBuf::from("proto"), protobuf_include];
    let canonical_output = PathBuf::from(std::env::var_os("OUT_DIR").expect("Cargo output path"))
        .join("canonical-grpc");
    std::fs::create_dir_all(&canonical_output).expect("canonical protobuf output directory");
    tonic_prost_build::configure()
        .out_dir(canonical_output)
        .build_client(true)
        .build_server(true)
        .generate_default_stubs(true)
        .btree_map(".nakama.api")
        .compile_well_known_types(true)
        .compile_protos(&protos, &includes)
        .expect("the pinned Nakama Healthcheck/auth protobuf subset must compile");
}
