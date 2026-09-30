// Positive control only. Never copy this file into a benchmark arm.
package ecr

import (
	"context"
	"encoding/base64"
	"errors"
	"net/url"
	"strings"
	"sync"
	"time"

	"github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/service/ecr"
	"oras.land/oras-go/v2/registry/remote/auth"
)

var ErrNoAWSECRAuthorizationData = errors.New("no AWS ECR authorization data")

type Client interface {
	GetAuthorizationToken(ctx context.Context, params *ecr.GetAuthorizationTokenInput, optFns ...func(*ecr.Options)) (*ecr.GetAuthorizationTokenOutput, error)
}

type cachedCredential struct {
	value   auth.Credential
	expires time.Time
}

type credentialFlight struct {
	done    chan struct{}
	value   auth.Credential
	expires time.Time
	err     error
}

type ECR struct {
	mu       sync.Mutex
	client   Client
	cache    map[string]cachedCredential
	inflight map[string]*credentialFlight
}

func (e *ECR) CredentialFunc(registry string) auth.CredentialFunc {
	return func(ctx context.Context, hostport string) (auth.Credential, error) {
		if hostport != registry {
			return auth.EmptyCredential, nil
		}
		return e.Credential(ctx, hostport)
	}
}

func (e *ECR) Credential(ctx context.Context, hostport string) (auth.Credential, error) {
	if err := ctx.Err(); err != nil {
		return auth.EmptyCredential, err
	}
	e.mu.Lock()
	if cached, ok := e.cache[hostport]; ok && time.Now().Before(cached.expires) {
		e.mu.Unlock()
		return cached.value, nil
	}
	if e.inflight == nil {
		e.inflight = make(map[string]*credentialFlight)
		e.cache = make(map[string]cachedCredential)
	}
	flight := e.inflight[hostport]
	if flight == nil {
		flight = &credentialFlight{done: make(chan struct{})}
		e.inflight[hostport] = flight
		go e.refresh(context.WithoutCancel(ctx), hostport, flight)
	}
	e.mu.Unlock()
	select {
	case <-ctx.Done():
		return auth.EmptyCredential, ctx.Err()
	case <-flight.done:
		if err := ctx.Err(); err != nil {
			return auth.EmptyCredential, err
		}
		if flight.err == nil && !flight.expires.IsZero() && !time.Now().Before(flight.expires) {
			return auth.EmptyCredential, errors.New("ECR authorization token has expired")
		}
		return flight.value, flight.err
	}
}

func (e *ECR) refresh(ctx context.Context, host string, flight *credentialFlight) {
	value, expires, err := fetchForRegistry(ctx, host)
	e.mu.Lock()
	defer e.mu.Unlock()
	flight.value, flight.expires, flight.err = value, expires, err
	if err == nil && !expires.IsZero() && time.Now().Before(expires) {
		e.cache[host] = cachedCredential{value, expires}
	} else {
		delete(e.cache, host)
	}
	delete(e.inflight, host)
	close(flight.done)
}

func fetchForRegistry(ctx context.Context, host string) (auth.Credential, time.Time, error) {
	cfg, err := config.LoadDefaultConfig(ctx)
	if err != nil {
		return auth.EmptyCredential, time.Time{}, err
	}
	output, err := ecr.NewFromConfig(cfg).GetAuthorizationToken(ctx, &ecr.GetAuthorizationTokenInput{})
	if err != nil {
		return auth.EmptyCredential, time.Time{}, err
	}
	if output == nil || len(output.AuthorizationData) == 0 {
		return auth.EmptyCredential, time.Time{}, ErrNoAWSECRAuthorizationData
	}
	for _, record := range output.AuthorizationData {
		if record.ProxyEndpoint == nil {
			continue
		}
		endpoint, err := url.Parse(*record.ProxyEndpoint)
		if err != nil || endpoint.Scheme != "https" || endpoint.Host != host || (endpoint.Path != "" && endpoint.Path != "/") {
			continue
		}
		var expires time.Time
		if record.ExpiresAt != nil {
			expires = *record.ExpiresAt
			if !time.Now().Before(expires) {
				return auth.EmptyCredential, expires, errors.New("ECR authorization token has expired")
			}
		}
		value, err := decodeToken(record.AuthorizationToken)
		return value, expires, err
	}
	return auth.EmptyCredential, time.Time{}, errors.New("no ECR authorization data for requested registry")
}

func decodeToken(token *string) (auth.Credential, error) {
	if token == nil {
		return auth.EmptyCredential, auth.ErrBasicCredentialNotFound
	}
	decoded, err := base64.StdEncoding.DecodeString(*token)
	if err != nil {
		return auth.EmptyCredential, err
	}
	user, pass, ok := strings.Cut(string(decoded), ":")
	if !ok {
		return auth.EmptyCredential, auth.ErrBasicCredentialNotFound
	}
	return auth.Credential{Username: user, Password: pass}, nil
}

// Preserve the existing uncached, low-level decoding seam and its error identity.
func (e *ECR) fetchCredential(ctx context.Context) (auth.Credential, error) {
	output, err := e.client.GetAuthorizationToken(ctx, &ecr.GetAuthorizationTokenInput{})
	if err != nil {
		return auth.EmptyCredential, err
	}
	if output == nil || len(output.AuthorizationData) == 0 {
		return auth.EmptyCredential, ErrNoAWSECRAuthorizationData
	}
	return decodeToken(output.AuthorizationData[0].AuthorizationToken)
}
