# Terraform-managed subset of the demo estate. Applied once (make tf-apply) to produce a local
# tfstate, then perturbed out of band (make tf-perturb) so the drift scenario compares real
# intended state against real live state. Everything is tagged purpose=oape-demo via default_tags.

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.40"
    }
  }
}

variable "region" {
  type    = string
  default = "ap-southeast-2"
}

variable "prefix" {
  type    = string
  default = "oape-demo"
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      purpose    = "oape-demo"
      managed-by = "terraform"
    }
  }
}

data "aws_caller_identity" "me" {}

data "aws_vpc" "default" {
  default = true
}

data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-6.1-x86_64"
}

resource "aws_s3_bucket" "tf_a" {
  bucket = "${var.prefix}-tf-a-${data.aws_caller_identity.me.account_id}"
  tags = {
    Name = "${var.prefix}-tf-a"
    env  = "prod"
  }
}

resource "aws_s3_bucket_versioning" "tf_a" {
  bucket = aws_s3_bucket.tf_a.id
  versioning_configuration {
    status = "Suspended"
  }
}

resource "aws_s3_bucket" "tf_b" {
  bucket = "${var.prefix}-tf-b-${data.aws_caller_identity.me.account_id}"
  tags = {
    Name = "${var.prefix}-tf-b"
    env  = "prod"
  }
}

resource "aws_security_group" "tf_app" {
  name        = "${var.prefix}-tf-app-sg"
  description = "oape demo: terraform-managed app tier"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "https from corporate ranges"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/8"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.prefix}-tf-app-sg"
    env  = "prod"
  }
}

resource "aws_instance" "tf_app" {
  ami                    = data.aws_ssm_parameter.al2023.value
  instance_type          = "t3.nano"
  vpc_security_group_ids = [aws_security_group.tf_app.id]

  metadata_options {
    http_tokens = "required"
  }

  tags = {
    Name  = "${var.prefix}-tf-app"
    env   = "prod"
    owner = "platform-team"
  }
}

output "instance_id" {
  value = aws_instance.tf_app.id
}

output "security_group_id" {
  value = aws_security_group.tf_app.id
}

output "buckets" {
  value = [aws_s3_bucket.tf_a.bucket, aws_s3_bucket.tf_b.bucket]
}
