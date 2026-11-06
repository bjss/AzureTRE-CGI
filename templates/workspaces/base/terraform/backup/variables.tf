variable "location" {
  type = string
}
variable "tre_id" {
  type = string
}
variable "resource_group_name" {
  type = string
}
variable "tre_workspace_tags" {
  type = map(string)
}
variable "tre_resource_id" {
  type = string
}
variable "enable_cmk_encryption" {
  type = bool
}

variable "vm_backup_policy" {
  type = object({
    timezone         = optional(string, "UTC")
    time             = optional(string, "22:00")
    daily_count      = optional(number, 14)
    weekly_count     = optional(number, 4)
    weekly_weekdays  = optional(list(string), ["Sunday"])
    monthly_count    = optional(number, 12)
    monthly_weekdays = optional(list(string), ["Monday"])
    monthly_weeks    = optional(list(string), ["First"])
    yearly_count     = optional(number, 2)
    yearly_weekdays  = optional(list(string), ["Sunday"])
    yearly_weeks     = optional(list(string), ["Last"])
    yearly_months    = optional(list(string), ["December"])
  })
}

variable "fileshare_backup_policy" {
  type = object({
    timezone         = optional(string, "UTC")
    time             = optional(string, "23:00")
    daily_count      = optional(number, 14)
    weekly_count     = optional(number, 4)
    weekly_weekdays  = optional(list(string), ["Sunday"])
    monthly_count    = optional(number, 12)
    monthly_weekdays = optional(list(string), ["Monday"])
    monthly_weeks    = optional(list(string), ["First"])
    yearly_count     = optional(number, 2)
    yearly_weekdays  = optional(list(string), ["Sunday"])
    yearly_weeks     = optional(list(string), ["Last"])
    yearly_months    = optional(list(string), ["December"])
  })
}
