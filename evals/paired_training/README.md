# Paired `train-fasttext` benchmark fixture

This directory freezes environment preparation for a same-task comparison on
Terminal-Bench 2.1 `train-fasttext`. It does not run Codex or any other paid
model. Both arms must be copied from one derived offline agent root filesystem
and must report identical full-root, `/app`, public-input, and tool hashes.

The source task is commit `7131e4375048a0e408a8fb404b5f499d726b695b`
and the Linux/amd64 image is pinned by digest in `config.json`. The image
already contains the public 650k-row Yelp training data and a separately
sampled public 10k subset. The private 40k archive is never copied into an
agent root filesystem.

## Trusted preparation (WSL2/Linux root)

Install `skopeo` and `umoci`, then fetch and unpack the exact image:

```sh
skopeo copy --override-os linux --override-arch amd64 \
  docker://alexgshaw/train-fasttext@sha256:3c77da9617e7fa577de04aef73fb1ac3114be32d7ffef72609c83929d842b157 \
  oci:/var/lib/cmr-train-fasttext/image:task
umoci unpack --image /var/lib/cmr-train-fasttext/image:task \
  /var/lib/cmr-train-fasttext/source
python3 evals/scripts/prepare_training_fixture.py verify-source \
  --config evals/paired_training/config.json \
  --rootfs /var/lib/cmr-train-fasttext/source/rootfs
```

Build the pinned, statically linked fastText CLI before introducing private
data, then derive the single offline agent rootfs. The derivation adds only
that public tool; it adds no compiler cache, recipe, model, tests, or oracle
material:

```sh
python3 evals/scripts/prepare_training_oracle.py \
  --config evals/paired_training/config.json \
  --destination /var/lib/cmr-train-fasttext/oracle-fasttext
python3 evals/scripts/prepare_training_fixture.py derive-agent-rootfs \
  --config evals/paired_training/config.json \
  --source-rootfs /var/lib/cmr-train-fasttext/source/rootfs \
  --fasttext-binary /var/lib/cmr-train-fasttext/oracle-fasttext/fasttext \
  --destination /var/lib/cmr-train-fasttext/agent-rootfs \
  --receipt /var/lib/cmr-train-fasttext/agent-rootfs-receipt.json
```

Create both arms from that derived root. Destinations must not exist:

```sh
python3 evals/scripts/prepare_training_fixture.py materialize-pair \
  --config evals/paired_training/config.json \
  --source-rootfs /var/lib/cmr-train-fasttext/agent-rootfs \
  --baseline-rootfs /var/lib/cmr-train-fasttext/arms/baseline \
  --treatment-rootfs /var/lib/cmr-train-fasttext/arms/treatment \
  --receipt /var/lib/cmr-train-fasttext/arms/fixture.json
```

The launcher uses user, mount, PID, and network namespaces followed by
`chroot`; only loopback exists and it is left down. It uses CPU affinity and
`RLIMIT_AS` for the 1-CPU and 4-GiB limits. It refuses to launch unless the arm
resides on a filesystem whose total capacity is at most 10 GiB; a directory on
a larger filesystem is not treated as storage isolation. Run `namespace-probe`
and `resource-probe`
before a paid campaign. API access for a future model runner must remain
outside this namespace; tool commands execute inside it. The runner design is
intentionally not claimed complete while dispatch-audit provenance and the
semantic task review remain open.

Host-specific feasibility and the current paid-run blocker are recorded in
[`ENVIRONMENT.md`](ENVIRONMENT.md). The fixture is preparation evidence, not
authorization to run the pair.

## Capped arm resources

`provision_training_resources.py` has fixed targets under
`/var/lib/cmr-train-fasttext/resource-sandbox-v1`. Its `provision` mode is
exclusive and fail-closed; it never overwrites or cleans up an existing path.
Its `verify` mode is read-only:

```sh
python3 evals/scripts/provision_training_resources.py verify \
  --config evals/paired_training/config.json
```

Each mounted arm contains only a `rootfs` subdirectory copied from the pinned
derived environment. `run_training_scope.py` wraps future arm commands in a
transient systemd scope with an aggregate one-CPU quota, 4-GiB memory cap,
zero swap allowance, 512-task cap, control-group termination, and a 3600-second
wall limit. A harmless effective-limit probe is available independently of any
model call:

```sh
python3 evals/scripts/run_training_scope.py probe --arm baseline
```

Current live state is fail-closed: the initial provisioning command verified
both mounts and copied trees, but a later independent `verify` invocation found
the mountpoints unmounted. The images, loop associations, UUIDs, and receipt
remain intact. Do not run an arm until a separately approved recovery step
re-establishes and re-verifies mount persistence.

## Trusted oracle

The verifier is statically linked so it remains ABI-compatible with the pinned
Debian task image. The receipt records the source commit, compiler, flags, and
binary hash.

The positive calibration model is built independently from the public 650k
training parquet with `build_training_reference.py`; it is stored only in the
trusted oracle area. It is not copied into an arm. This is calibration data,
not a model-run input or a benchmark answer exposed to an agent.

The first independent attempt was safely graded but reached only `0.572525`,
below the `0.62` threshold. `calibration.json` records that failed control and
the passing empty negative. No reference-positive calibration is claimed; the
paid pair remains blocked.

Stage G subsequently ran exactly four predeclared public-only configurations
from `public-calibration-plan.json` under an aggregate one-CPU/4-GiB scope. The
best exact public-validation accuracy was `0.6224`; all models met the size
limit, but none met the frozen `0.63` public selection gate. Consequently no
candidate was selected and the private verifier was called zero times. See
`public-calibration-result.json` for the aggregate public results.

Grade only after an arm is closed. The grader rejects links and non-regular
artifacts and captures only `/app/model.bin` using one no-follow descriptor.
It bounded-copies and hashes that descriptor
into an evaluator-owned mode-`0400` snapshot and rejects growth, mutation, or
pathname replacement before grading the snapshot. Candidate lookup is fixed
to `app/model.bin` relative to no-follow candidate-root and `app` directory
descriptors, so a symlinked `app` cannot redirect capture outside the arm. It
binds the verifier,
config, grader, and clean source-root hashes, verifies the opaque archive
commitment, runs with networking disabled, and emits aggregate accuracy, size,
and hashes only. The trusted root and private material must be root-owned mode
`0700`/`0600` outside all agent workspaces.

fastText `v0.9.2` renders `P@1` with three significant digits. Reports retain
the exact `correct / 40000` value and exact threshold result separately from
the official CLI-rendered value and its threshold result. `quality_pass` uses
the official CLI result without changing the configured `0.62` threshold.

```sh
python3 evals/scripts/grade_training_artifact.py \
  --config evals/paired_training/config.json \
  --expected-config-sha256 67069c3a6140ada51a32ff63f203e839c0f479ed856879c0ce26fe1a0f6c523e \
  --candidate-rootfs /var/lib/cmr-train-fasttext/arms/baseline \
  --source-rootfs /var/lib/cmr-train-fasttext/agent-rootfs \
  --private-archive /var/lib/cmr-train-fasttext/private_test.tar.gz \
  --fasttext-binary /var/lib/cmr-train-fasttext/oracle-fasttext/fasttext \
  --output /var/lib/cmr-train-fasttext/results/baseline.json
```

The expected config digest comes from the separately frozen
`config.sha256`; the grader checks it against the raw config bytes before
parsing thresholds, paths, or verifier commitments.

Do not place the private archive, extracted examples, reference model, gold
solution, or upstream verifier tests in either arm. Publish the aggregate
result only after the verifier process exits and its temporary directory has
been removed.
