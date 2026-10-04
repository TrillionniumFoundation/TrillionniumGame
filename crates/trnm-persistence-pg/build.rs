#![forbid(unsafe_code)]

use std::path::PathBuf;

mod authoritative_schema_build {
    include!("schema_build.rs");
}

fn main() {
    authoritative_schema_build::generate();
    println!("cargo:rerun-if-changed=proto/nakama-healthcheck.proto");

    let protoc = protoc_bin_vendored::protoc_bin_path()
        .expect("the reviewed vendored protoc package must provide this target binary");
    let protobuf_include = protoc_bin_vendored::include_path()
        .expect("the reviewed vendored protoc package must provide well-known types");
    std::env::set_var("PROTOC", protoc);

    let protos = [PathBuf::from("proto/nakama-healthcheck.proto")];
    let includes = [PathBuf::from("proto"), protobuf_include];
    tonic_prost_build::configure()
        .build_client(true)
        .build_server(true)
        .compile_well_known_types(true)
        .compile_protos(&protos, &includes)
        .expect("the pinned Nakama Healthcheck protobuf subset must compile");

    // The existing lib-test composes the real canonical runtime sources. Keep
    // its generated auth bindings separate from this diagnostic binary's
    // Healthcheck-only bindings; no duplicate protocol source or fake service.
    let canonical_proto = PathBuf::from("../trnm-server/proto/nakama-healthcheck.proto");
    println!("cargo:rerun-if-changed={}", canonical_proto.display());
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
        .compile_protos(
            &[canonical_proto],
            &[PathBuf::from("../trnm-server/proto"), includes[1].clone()],
        )
        .expect(
            "the canonical runtime protobuf subset must compile for its native test composition",
        );
}
