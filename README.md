# Cycler

Cycler fills a very specific need:

> I like my Kubernetes deployments to use the `latest` tag. How do I 
> restart my deployments whenever it gets updated in the registry?

Cycler monitors deployments annotated with `"app.cycler.io/enable": "true"` and triggers
a `rollout restart` whenever one of their containers' images becomes out of date.

## Installation

You can install Cycler with the helm chart in this repo. The default settings should work,
but configuration is documented below.

## The rate-limiting problem

Some registries, specifically Docker Hub, limit the amount of pulls you can perform [per day](https://docs.docker.com/docker-hub/download-rate-limit/).
The process of getting the latest digest SHA for a given image **counts against** these limits.

To solve this problem, Cycler allows per-registry request limiting. Docker Hub is configured
to only check once per hour, by default. See [configuration](Configuration) below for more
information.

## Configuration

Cycler is configured by default using [`values.yaml`](./helm/cycler/values.yaml).
You can mount your own configmap and override the target file by setting the
`CYCLER_CONFIG` environment variable

### Environment Variables

| Variable | Description | Default
|-|-|-|
| CYCLER_CONFIG | Configuration file to be read | `/etc/cycler/config.yml`

### `values.yaml`
In addition to fairly standard Helm chart variables (see the [chart](./helm/cycler) for more info), the following values can be set under [`config`](./helm/cycler/values.yaml#L114) in your `values.yaml`

| Param | Description | Default
|-|-|-|
| `scanDelay` | Time to wait between scans | 5 seconds
| `registryTimeout` | How long should requests to regiestires be allowed to take (prevents the program from locking up due to rate limiting) | 4 seconds
| `rates` | How long to wait between requests to a specific domain, in seconds
| `rates."docker.io"` | rate limitation for docker.io. Default is 1 hour | 3600 seconds
| EXAMPLE `rates."domain.com"` | Would limit how often `domain.com` is queried when scanning, in seconds |
| `startRated` | Should Cycler start with domain-specific rates set (`true`), or check all domains on its first scan (`false`)? | `false`
| `loglevel` | Log verbosity. Set to either `"INFO"` (low) or `"DEBUG"` (high) | `"INFO"`
| `notifications` | notification parameters. The following services are supported. `null` values disable a service
| `notifications.discord` | Discord webhook url to notify when a service is restarted | `null`