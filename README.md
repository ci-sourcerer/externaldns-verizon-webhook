# ExternalDNS Verizon Webhook Provider

## Overview

This web server implements a Kubernetes ExternalDNS webhook provider for managing DNS records on a Verizon Fios router (CR1000A/CR1000B). Use case: You have a home lab running Kubernetes on a network with a Verizon Fios router, and you want to manage internal DNS records automatically.

It follows the Kubernetes ExternalDNS webhook provider model. See the [upstream tutorial](https://kubernetes-sigs.github.io/external-dns/latest/docs/tutorials/webhook-provider/) for configuration and deployment details.

All images are published and tagged by release at `docker.io/cisourcerer/externaldns-verizon-webhook:<tag>` (replace `<tag>` with the desired release tag when pulling or referencing the image).
