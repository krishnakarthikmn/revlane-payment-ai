variable "region" {
  description = "One region. Mumbai keeps the data in India."
  type        = string
  default     = "ap-south-1"
}

variable "availability_zones" {
  type    = list(string)
  default = ["ap-south-1a", "ap-south-1b"]
}

variable "environment" {
  type    = string
  default = "staging"
}

variable "database_instance_class" {
  type    = string
  default = "db.t4g.medium"
}

variable "services" {
  description = "The three things that run: the API, the worker and the web app."
  type = map(object({
    image   = string
    command = list(string)
    port    = optional(number)
    cpu     = number
    memory  = number
    count   = number
  }))
}
