# Revlane in one region: AWS Mumbai. A stub: never applied, never validated.
# See docs/deployment.md.

terraform {
  required_version = ">= 1.9"
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
    tags = { project = "revlane", environment = var.environment }
  }
}

locals {
  name = "revlane-${var.environment}"
}

# --- Network -------------------------------------------------------------------

resource "aws_vpc" "main" {
  cidr_block           = "10.20.0.0/16"
  enable_dns_hostnames = true
}

resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(aws_vpc.main.cidr_block, 4, count.index)
  availability_zone = var.availability_zones[count.index]
}

resource "aws_security_group" "tasks" {
  name   = "${local.name}-tasks"
  vpc_id = aws_vpc.main.id
}

resource "aws_security_group" "database" {
  name   = "${local.name}-database"
  vpc_id = aws_vpc.main.id

  ingress {
    description     = "Postgres from the application tasks only"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.tasks.id]
  }
}

# TODO: public subnets, a load balancer with a certificate, and NAT or VPC
# endpoints so tasks can pull images and reach the model provider.

# --- Encryption and secrets --------------------------------------------------------

resource "aws_kms_key" "main" {
  description         = "Revlane: database, bucket and secrets"
  enable_key_rotation = true
}

resource "aws_secretsmanager_secret" "app" {
  for_each   = toset(["database-url", "token-hash-root-key", "vault-key", "llm-api-key", "session-secret"])
  name       = "${local.name}/${each.key}"
  kms_key_id = aws_kms_key.main.arn
}

# --- Database -------------------------------------------------------------------------

resource "aws_db_subnet_group" "main" {
  name       = local.name
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_instance" "main" {
  identifier                  = local.name
  engine                      = "postgres"
  engine_version              = "16"
  instance_class              = var.database_instance_class
  allocated_storage           = 50
  storage_encrypted           = true
  kms_key_id                  = aws_kms_key.main.arn
  db_name                     = "revlane"
  username                    = "revlane"
  manage_master_user_password = true
  db_subnet_group_name        = aws_db_subnet_group.main.name
  vpc_security_group_ids      = [aws_security_group.database.id]
  backup_retention_period     = 7
  deletion_protection         = true
  publicly_accessible         = false
  skip_final_snapshot         = false
  final_snapshot_identifier   = "${local.name}-final"
}

# --- Drafted claims and reports ------------------------------------------------------------

resource "aws_s3_bucket" "documents" {
  bucket = "${local.name}-documents"
}

resource "aws_s3_bucket_public_access_block" "documents" {
  bucket                  = aws_s3_bucket.documents.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.main.arn
    }
  }
}

# --- Services ------------------------------------------------------------------------------

resource "aws_ecs_cluster" "main" {
  name = local.name
}

resource "aws_cloudwatch_log_group" "services" {
  for_each          = var.services
  name              = "/revlane/${var.environment}/${each.key}"
  retention_in_days = 90
}

resource "aws_ecs_task_definition" "service" {
  for_each                 = var.services
  family                   = "${local.name}-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = each.value.cpu
  memory                   = each.value.memory
  # TODO: execution and task roles. The task role needs the documents bucket,
  # the four secrets and the KMS key, and nothing else.

  container_definitions = jsonencode([{
    name      = each.key
    image     = each.value.image
    command   = each.value.command
    essential = true
    portMappings = each.value.port == null ? [] : [{ containerPort = each.value.port }]
    environment = [
      { name = "LLM_STUB", value = "true" },
      { name = "PROVIDER_LIVE_CALLS", value = "false" },
      { name = "AGENT_RUNS_INLINE", value = "false" },
      { name = "SESSION_COOKIE_SECURE", value = "true" },
    ]
    secrets = each.key == "web" ? [] : [
      { name = "DATABASE_URL", valueFrom = aws_secretsmanager_secret.app["database-url"].arn },
      { name = "TOKEN_HASH_ROOT_KEY", valueFrom = aws_secretsmanager_secret.app["token-hash-root-key"].arn },
      { name = "VAULT_KEY", valueFrom = aws_secretsmanager_secret.app["vault-key"].arn },
      { name = "LLM_API_KEY", valueFrom = aws_secretsmanager_secret.app["llm-api-key"].arn },
      { name = "SESSION_SECRET", valueFrom = aws_secretsmanager_secret.app["session-secret"].arn },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.services[each.key].name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = each.key
      }
    }
  }])
}

resource "aws_ecs_service" "service" {
  for_each        = var.services
  name            = each.key
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.service[each.key].arn
  desired_count   = each.value.count
  launch_type     = "FARGATE"

  network_configuration {
    subnets         = aws_subnet.private[*].id
    security_groups = [aws_security_group.tasks.id]
  }
  # TODO: attach the api and web services to the load balancer.
}

# TODO: an EventBridge schedule that runs the worker image daily with the
# command ["python", "-m", "revlane.lifecycle", "retention"].
