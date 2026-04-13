terraform {
  required_providers {
    schemaregistry = {
      source  = "Telefonica/schema-registry"
      version = "~> 2.0"
    }
  }
}

variable "subject" {
  description = "Schema Registry subject name (e.g. txn.fraud.alerts-value)"
  type        = string
}

variable "schema_file" {
  description = "Path to .avsc Avro schema file"
  type        = string
}

variable "compatibility_level" {
  description = "Compatibility mode (must be BACKWARD_TRANSITIVE for this project)"
  type        = string
  default     = "BACKWARD_TRANSITIVE"
}

resource "schemaregistry_schema" "this" {
  subject     = var.subject
  schema      = file(var.schema_file)
  schema_type = "AVRO"
}

resource "schemaregistry_subject_config" "this" {
  subject             = var.subject
  compatibility_level = var.compatibility_level
  depends_on          = [schemaregistry_schema.this]
}

output "subject" {
  value = schemaregistry_schema.this.subject
}

output "schema_id" {
  value = schemaregistry_schema.this.schema_id
}
