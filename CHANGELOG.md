# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
### Fixed
### Changed
### Removed


## [2.0.0]

### Added

- **PODAAC-6461**: Added additional Terraform tooling to enable one command deploys
- **PODAAC-6461**: Ported SWODLR's lambda build script and utilities
- **PODAAC-6461**: EventMessage validates on inputs
- **PODAAC-6461**: Mute Mode enables silent Sigevent deployments for UAT/SIT

### Fixed

- **PODAAC-6271**: Email subscriptions no longer overwritten by deploys

### Changed

- **PODAAC-6461**: Refactored architecture away from DynamoDB as primary store to CloudWatch
- **PODAAC-6461**: Switched SNS email subscriptions to SES emails
- **PODAAC-6461**: Refactored daily report generator to generate broader analysis of events
- **PODAAC-6461**: Restructured Terraform to organize resources by scope

### Removed

- **PODAAC-6461**: Removed requirement for DynamoDB ORM
- **PODAAC-6461**: Removed unnecessary functions from EventMessage object
- **PODAAC-6461**: Removed DynamoDB schemas


## [1.0.0]

### Added
- **PODAAC-5894**: Initial push of terraform code
- **PODAAC-5895**: Initial commit of event-driven Cloud Sigevent code
- Added support for TTL
- **PODAAC-5896**: CI/CD
- Created tfvars for sit, uat, and ops
- **PODAAC-5898**: Initial commit of daily sigevent report
- Allow MODIS L3 client to publish to Sigevent topic
- Allow S3 Cleanup client to publish to Sigevent topic
### Fixed
- Only use first 100 chars from subject when publishing to SNS
### Changed
- **PODAAC-5895**: Parametrized DynamoDB table name
- Renamed sigevent fields
- Parametrized client arns into tfvars for easier integration
### Removed
