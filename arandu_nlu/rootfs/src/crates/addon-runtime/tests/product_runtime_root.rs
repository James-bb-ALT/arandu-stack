use std::fs;
use std::os::unix::fs::{MetadataExt, PermissionsExt, chown, symlink};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

pub use addon_runtime::RuntimeError;
use nlu_server::PeerIdentity;

pub type Result<T> = std::result::Result<T, RuntimeError>;

#[path = "../src/process.rs"]
mod process;

use process::{DEFAULT_GENERATION_ROOT, RuntimeIdentities, prepare_generation_root_under};

const FIXTURE_TECNICA_MODE: u32 = 0o770;
static FIXTURE_TECNICA_SEQUENCE: AtomicU64 = AtomicU64::new(1);
const _: fn(RuntimeIdentities) -> Result<PathBuf> = process::prepare_product_generation_root;

fn fixture_tecnica_anchor(label: &str) -> PathBuf {
    let parent = fs::canonicalize(std::env::temp_dir())
        .expect("FIXTURE_TECNICA canonical temporary directory")
        .join("FIXTURE_TECNICA_product_runtime_root");
    fs::create_dir_all(&parent).expect("FIXTURE_TECNICA fixture parent");
    let sequence = FIXTURE_TECNICA_SEQUENCE.fetch_add(1, Ordering::Relaxed);
    let anchor = parent.join(format!(
        "FIXTURE_TECNICA_{label}_{}_{}",
        std::process::id(),
        sequence
    ));
    fs::create_dir(&anchor).expect("FIXTURE_TECNICA fixture anchor");
    anchor
}

fn fixture_tecnica_identities() -> RuntimeIdentities {
    let current = PeerIdentity::current().expect("FIXTURE_TECNICA current identity");
    if current.uid() == 0 {
        return RuntimeIdentities::product().expect("FIXTURE_TECNICA product identities");
    }
    assert_ne!(
        current.gid(),
        0,
        "FIXTURE_TECNICA requires a non-root current group"
    );
    let adapter_primary_gid = distinct_nonzero(&[current.gid()]);
    let server_primary_gid = distinct_nonzero(&[current.gid(), adapter_primary_gid]);
    let server_uid = distinct_nonzero(&[current.uid()]);
    RuntimeIdentities::new(
        current.uid(),
        adapter_primary_gid,
        server_uid,
        server_primary_gid,
        current.gid(),
    )
    .expect("FIXTURE_TECNICA identities")
}

fn distinct_nonzero(excluded: &[u32]) -> u32 {
    (1..=u32::MAX)
        .find(|candidate| !excluded.contains(candidate))
        .expect("FIXTURE_TECNICA distinct identifier")
}

fn fixture_tecnica_generation_root(anchor: &Path) -> PathBuf {
    anchor
        .join("ptbr-nlu")
        .join("adapter-server")
        .join("generations")
}

fn assert_fixture_tecnica_managed_metadata(path: &Path, identities: RuntimeIdentities) {
    let metadata = fs::symlink_metadata(path).expect("FIXTURE_TECNICA managed metadata");
    assert!(metadata.is_dir(), "FIXTURE_TECNICA managed directory");
    assert!(!metadata.file_type().is_symlink());
    assert_eq!(metadata.permissions().mode() & 0o7777, FIXTURE_TECNICA_MODE);
    assert_eq!(metadata.uid(), identities.adapter().uid());
    assert_eq!(metadata.gid(), identities.ipc_gid());
}

#[test]
fn fixture_tecnica_product_root_is_one_exact_absolute_path() {
    let root = Path::new(DEFAULT_GENERATION_ROOT);
    assert!(root.is_absolute());
    assert_eq!(root, Path::new("/run/ptbr-nlu/adapter-server/generations"));
    assert_eq!(
        prepare_generation_root_under(
            Path::new("FIXTURE_TECNICA_relative_runtime_parent"),
            fixture_tecnica_identities(),
        )
        .expect_err("FIXTURE_TECNICA relative path must fail"),
        RuntimeError::InvalidProcessConfiguration
    );
}

#[test]
fn fixture_tecnica_preparation_is_idempotent_and_preserves_stale_generations() {
    let anchor = fixture_tecnica_anchor("idempotent");
    let identities = fixture_tecnica_identities();
    let expected_root = fixture_tecnica_generation_root(&anchor);

    let root = prepare_generation_root_under(&anchor, identities)
        .expect("FIXTURE_TECNICA prepare missing tree");
    assert_eq!(root, expected_root);
    for path in [
        anchor.join("ptbr-nlu"),
        anchor.join("ptbr-nlu").join("adapter-server"),
        expected_root.clone(),
    ] {
        assert_fixture_tecnica_managed_metadata(&path, identities);
    }

    let stale_path = root.join("generation-0000000000000001");
    fs::create_dir(&stale_path).expect("FIXTURE_TECNICA stale generation");
    fs::set_permissions(
        &stale_path,
        fs::Permissions::from_mode(FIXTURE_TECNICA_MODE),
    )
    .expect("FIXTURE_TECNICA stale mode");
    let stale_metadata =
        fs::symlink_metadata(&stale_path).expect("FIXTURE_TECNICA initial stale metadata");
    if stale_metadata.uid() != identities.adapter().uid()
        || stale_metadata.gid() != identities.ipc_gid()
    {
        chown(
            &stale_path,
            Some(identities.adapter().uid()),
            Some(identities.ipc_gid()),
        )
        .expect("FIXTURE_TECNICA stale ownership");
    }
    let stale_inode = fs::symlink_metadata(&stale_path)
        .expect("FIXTURE_TECNICA stale metadata")
        .ino();

    assert_eq!(
        prepare_generation_root_under(&anchor, identities)
            .expect("FIXTURE_TECNICA idempotent preparation"),
        expected_root
    );
    assert_eq!(
        fs::symlink_metadata(&stale_path)
            .expect("FIXTURE_TECNICA preserved stale metadata")
            .ino(),
        stale_inode
    );

    fs::remove_dir_all(anchor).expect("FIXTURE_TECNICA cleanup");
}

#[test]
fn fixture_tecnica_symlinks_and_non_directories_fail_closed() {
    let identities = fixture_tecnica_identities();

    let symlink_anchor = fixture_tecnica_anchor("symlink");
    let symlink_target = symlink_anchor.join("FIXTURE_TECNICA_symlink_target");
    fs::create_dir(&symlink_target).expect("FIXTURE_TECNICA symlink target");
    symlink(&symlink_target, symlink_anchor.join("ptbr-nlu"))
        .expect("FIXTURE_TECNICA managed symlink");
    assert_eq!(
        prepare_generation_root_under(&symlink_anchor, identities)
            .expect_err("FIXTURE_TECNICA symlink must fail"),
        RuntimeError::RuntimeDirectorySecurity
    );
    fs::remove_dir_all(symlink_anchor).expect("FIXTURE_TECNICA symlink cleanup");

    let file_anchor = fixture_tecnica_anchor("non_directory");
    fs::write(
        file_anchor.join("ptbr-nlu"),
        b"FIXTURE_TECNICA non-directory",
    )
    .expect("FIXTURE_TECNICA non-directory");
    assert_eq!(
        prepare_generation_root_under(&file_anchor, identities)
            .expect_err("FIXTURE_TECNICA non-directory must fail"),
        RuntimeError::RuntimeDirectorySecurity
    );
    fs::remove_dir_all(file_anchor).expect("FIXTURE_TECNICA file cleanup");
}

#[test]
fn fixture_tecnica_foreign_entries_and_insecure_metadata_are_not_repaired() {
    let identities = fixture_tecnica_identities();

    let foreign_anchor = fixture_tecnica_anchor("foreign");
    let root = prepare_generation_root_under(&foreign_anchor, identities)
        .expect("FIXTURE_TECNICA prepare foreign tree");
    let adapter_server = foreign_anchor.join("ptbr-nlu").join("adapter-server");
    fs::write(
        adapter_server.join("FIXTURE_TECNICA_foreign"),
        b"FIXTURE_TECNICA foreign entry",
    )
    .expect("FIXTURE_TECNICA foreign entry");
    assert_eq!(
        prepare_generation_root_under(&foreign_anchor, identities)
            .expect_err("FIXTURE_TECNICA foreign entry must fail"),
        RuntimeError::RuntimeDirectorySecurity
    );
    assert!(root.is_dir(), "FIXTURE_TECNICA root remains present");
    fs::remove_dir_all(foreign_anchor).expect("FIXTURE_TECNICA foreign cleanup");

    let mode_anchor = fixture_tecnica_anchor("mode");
    prepare_generation_root_under(&mode_anchor, identities)
        .expect("FIXTURE_TECNICA prepare mode tree");
    let managed = mode_anchor.join("ptbr-nlu");
    fs::set_permissions(&managed, fs::Permissions::from_mode(0o750))
        .expect("FIXTURE_TECNICA insecure mode");
    assert_eq!(
        prepare_generation_root_under(&mode_anchor, identities)
            .expect_err("FIXTURE_TECNICA insecure mode must fail"),
        RuntimeError::RuntimeDirectorySecurity
    );
    assert_eq!(
        fs::symlink_metadata(&managed)
            .expect("FIXTURE_TECNICA unchanged metadata")
            .permissions()
            .mode()
            & 0o7777,
        0o750
    );
    fs::remove_dir_all(mode_anchor).expect("FIXTURE_TECNICA mode cleanup");
}
