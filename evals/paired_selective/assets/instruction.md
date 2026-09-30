A code repository is available in the `/app` directory. Your task is to implement the changes necessary to satisfy the following Pull Request (PR) description:

<pr_description>
# Title: OCI storage cannot authenticate against AWS ECR registries


## Description
Currently OCI storage can only authenticate against a registry with a username and a password written into configuration by hand, so bundles kept in an AWS ECR registry stay out of reach for anyone relying on the credentials their AWS environment already provides, and syncing stops once the token behind the stored credentials expires. Authentication settings that cannot work are accepted in silence.

## Requirements
- OCI storage authentication should carry a mode, with `static` keeping the present username and password behavior and `aws-ecr` drawing credentials from the ambient AWS environment, and only those two should count as valid.

- Loading should read authentication given without a mode as static, should accept the AWS ECR mode with no credentials, and should leave authentication unset when none is given.

- An unrecognized mode should fail validation with exactly `oci authentication type is not supported`, and asking for a credential option for one should be refused rather than assumed, so the mode `unknown` should give exactly `unsupported auth type unknown`.

- Store options should hold registry credentials as a resolver answering per registry rather than as a fixed pair, so either supported mode should leave it able to serve any registry, while the manifest version option should keep behaving as it does.

- ECR resolution should retrieve an authorization token through a client that can be substituted, and should fail when no AWS configuration can be loaded.

- A retrieved token should decode into the registry user and password, while a client error should come back unchanged, absent authorization data should raise a dedicated error, a token that is missing or carries no separator between user and password should raise the basic credential not found sentinel, and a token that is not valid base64 should return the decoding error.

- OCI storage authentication configuration must support a `type` value with the supported authentication modes `static` and `aws-ecr`.

- When OCI authentication uses static credentials, existing username and password behavior must continue to work. When an `authentication` block provides `username` or `password` but omits `type`, configuration loading must materialize the resulting authentication's `Type` as the static authentication mode, so that a loaded config without an explicit `type` still reports `IsValid() == true` and passes validation (rather than being treated as an empty, unsupported authentication type).

- When OCI authentication uses `type: aws-ecr`, configuration loading must not require `username` or `password`, and the resulting configuration must use the AWS ECR authentication mode.

- OCI storage configuration must also load successfully when no `authentication` block is provided.

- Configuration validation must reject unsupported OCI authentication types with the exact error message `oci authentication type is not supported`.

- Three OCI configuration example files must exist on disk at the exact paths `internal/config/testdata/storage/oci_provided_aws_ecr.yml`, `internal/config/testdata/storage/oci_provided_no_auth.yml`, and `internal/config/testdata/storage/oci_provided_invalid_auth.yml`. All three use the repository `some.target/repository/abundle:latest`, bundles directory `/tmp/bundles`, and a poll interval of 5 minutes.

- Loading `oci_provided_aws_ecr.yml` (whose `authentication` block sets `type: aws-ecr` and provides no username or password) must yield an OCI authentication whose `Type` equals the AWS-ECR authentication mode with empty username and password. Loading `oci_provided_no_auth.yml` (which has no `authentication` block) must yield OCI storage with a nil authentication value. Loading `oci_provided_invalid_auth.yml` (whose `authentication` block sets `type: invalid`) must fail validation with the exact error `oci authentication type is not supported`.

- `AuthenticationType.IsValid()` must return `true` for `static` and `aws-ecr`, and `false` for unsupported or empty authentication type values.

- `WithCredentials(kind, user, pass)` must support `static` authentication by producing credentials from the provided username and password, support `aws-ecr` authentication by producing AWS ECR-backed credentials, and return the exact error `unsupported auth type unknown` when called with `AuthenticationType("unknown")`.

- Applying a successful credential option to `StoreOptions` must populate an internal credential-function-factory field on `StoreOptions` named `auth`, of shape `func(registry string) oras.land/oras-go/v2/registry/remote/auth.CredentialFunc`. After the option runs, that `auth` field must be non-nil, and invoking it with any registry string must return a non-nil `auth.CredentialFunc`.

- AWS ECR credential resolution must request an authorization token through the configured ECR client and map outcomes as follows: client errors are returned unchanged, empty authorization data returns `ErrNoAWSECRAuthorizationData`, a nil token returns `auth.ErrBasicCredentialNotFound`, invalid base64 returns the base64 decoding error, a decoded token without a username and password separated by `:` returns `auth.ErrBasicCredentialNotFound`, and a valid decoded token returns matching `Username` and `Password` values.

- The ECR credential provider must return an error when AWS credentials cannot be loaded from the default AWS configuration chain.

- The `ECR` value must expose an internal (unexported) `client` field of type `Client`, where `Client` is a package-local interface in `internal/oci/ecr` describing a single method `GetAuthorizationToken(ctx context.Context, params *ecr.GetAuthorizationTokenInput, optFns ...func(*ecr.Options)) (*ecr.GetAuthorizationTokenOutput, error)` (matching the AWS SDK v2 `ecr` service surface). This field is the injection point through which an alternate ECR API client can be supplied.

- The `ECR` value must expose an internal (unexported) helper method with the exact signature `fetchCredential(ctx context.Context) (auth.Credential, error)` that performs the ECR token retrieval against the injected `client` and applies the same error-mapping rules already listed above (client errors returned unchanged; empty `AuthorizationData` returns `ErrNoAWSECRAuthorizationData`; nil `AuthorizationToken` returns `auth.ErrBasicCredentialNotFound`; invalid base64 returns the base64 decoding error; a decoded string without a `:` separator returns `auth.ErrBasicCredentialNotFound`; a valid `user:pass` decoded string returns matching `Username` and `Password`). The public `ECR.Credential` method must reduce to loading AWS configuration, constructing the concrete ECR client from that config and storing it on `client`, then delegating to `fetchCredential`.

- A mockery-generated mock of the package-local `Client` interface must live in the same package at path `internal/oci/ecr/mock_client.go`. It must expose a `MockClient` type wrapping `mock.Mock` (from `github.com/stretchr/testify/mock`), a mock implementation of `GetAuthorizationToken` matching the interface, and a constructor with the exact signature `NewMockClient(t interface{ mock.TestingT; Cleanup(func()) }) *MockClient` (the standard mockery output; regenerate with `mockery --name Client --filename mock_client.go` inside `internal/oci/ecr`).

- Declare `github.com/aws/aws-sdk-go-v2/service/ecr` (and any needed sibling modules) as normal `require` entries in go.mod and import the real packages (e.g. `.../service/ecr/types`). Do NOT write local shims or `replace` directives: although your sandbox is offline, modules are fetched from the Go module proxy when the project is built and tested, so write the code against the real API from knowledge.

## New Interfaces
- Path: `internal/oci/options.go`

- Name: `AuthenticationType.IsValid`

- Type: method

- Input: NA

- Output: `bool`

- Description: Public method that reports whether an OCI authentication type is supported, returning `true` for `static` and `aws-ecr` and `false` otherwise.

- Path: `internal/oci/options.go`

- Name: `WithStaticCredentials`

- Type: function

- Input: `user: string`, `pass: string`

- Output: `containers.Option[StoreOptions]`

- Description: Public function that creates an OCI store option using static registry credentials from the provided username and password.

- Path: `internal/oci/options.go`

- Name: `WithAWSECRCredentials`

- Type: function

- Input: NA

- Output: `containers.Option[StoreOptions]`

- Description: Public function that creates an OCI store option using AWS ECR-backed registry credential resolution.

- Path: `internal/oci/ecr/ecr.go`

- Name: `ECR`

- Type: struct

- Input: NA

- Output: NA

- Description: Public concrete type that provides AWS ECR-backed credential resolution for OCI registry authentication.

- Path: `internal/oci/ecr/ecr.go`

- Name: `ECR.CredentialFunc`

- Type: method

- Input: `registry: string`

- Output: `auth.CredentialFunc`

- Description: Public method that returns the ECR credential resolver as an ORAS credential function for the provided registry.

- Path: `internal/oci/ecr/ecr.go`

- Name: `ECR.Credential`

- Type: method

- Input: `ctx: context.Context`, `hostport: string`

- Output: `auth.Credential`, `error`

- Description: Public method that loads AWS configuration, resolves an ECR authorization token, and returns decoded registry credentials or a credential resolution error.
</pr_description>

Constraints:

* Do not reference, look up, or copy existing solutions, external PRs, or online workarounds. The implementation must be entirely your own independent work.
