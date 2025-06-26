terraform {
  required_version = ">= 1.5.7"

  backend "s3" {
    key = "podaac-sigevent/terraform.tfstate"
  }

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = local.default_tags
  }

  ignore_tags {
    key_prefixes = ["gsfc-ngap"]
  }
}

provider "aws" {
  alias = "ses_region"
  region = var.ses_region

  default_tags {
    tags = local.default_tags
  }

  ignore_tags {
    key_prefixes = ["gsfc-ngap"]
  }
}

data "aws_caller_identity" "current" {}

data "local_file" "pyproject_toml" {
  filename = abspath("${path.root}/../pyproject.toml")
}

data "aws_iam_policy" "permissions_boundary" {
  name = "NGAPShRoleBoundary"
}

locals {
  name         = regex("name = \"(\\S*)\"", data.local_file.pyproject_toml.content)[0]
  version      = regex("version = \"(\\S*)\"", data.local_file.pyproject_toml.content)[0]

  prefix       = "service-${var.service_name}-${var.environment}"
  service_path = "/service/${var.service_name}"

  default_tags = length(var.default_tags) == 0 ? {
    team        = "IA",
    application = local.prefix,
    environment = var.environment,
    version     = local.version
  } : var.default_tags
}
