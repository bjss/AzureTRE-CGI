mock_provider "azurerm" {}

variables {
  location                = "westeurope"
  tre_id                  = "test"
  tre_resource_id         = "12345678"
  resource_group_name     = "test"
  tre_workspace_tags      = {}
  enable_cmk_encryption   = false
  vm_backup_policy        = {}
  fileshare_backup_policy = {}
}

run "default_policies" {
  command = plan

  module {
    source = "./backup"
  }

  assert {
    condition = (
      azurerm_backup_policy_vm.vm_policy.timezone == "UTC" &&
      azurerm_backup_policy_vm.vm_policy.backup[0].frequency == "Daily" &&
      azurerm_backup_policy_vm.vm_policy.backup[0].time == "22:00" &&
      azurerm_backup_policy_vm.vm_policy.retention_daily[0].count == 14 &&
      azurerm_backup_policy_vm.vm_policy.retention_weekly[0].count == 4 &&
      contains(azurerm_backup_policy_vm.vm_policy.retention_weekly[0].weekdays, "Sunday") &&
      azurerm_backup_policy_vm.vm_policy.retention_monthly[0].count == 12 &&
      contains(azurerm_backup_policy_vm.vm_policy.retention_monthly[0].weekdays, "Monday") &&
      contains(azurerm_backup_policy_vm.vm_policy.retention_monthly[0].weeks, "First") &&
      azurerm_backup_policy_vm.vm_policy.retention_yearly[0].count == 2 &&
      contains(azurerm_backup_policy_vm.vm_policy.retention_yearly[0].weekdays, "Sunday") &&
      contains(azurerm_backup_policy_vm.vm_policy.retention_yearly[0].weeks, "Last") &&
      contains(azurerm_backup_policy_vm.vm_policy.retention_yearly[0].months, "December")
    )
    error_message = "The VM policy must preserve the original defaults."
  }

  assert {
    condition = (
      azurerm_backup_policy_file_share.file_share_policy.timezone == "UTC" &&
      azurerm_backup_policy_file_share.file_share_policy.backup[0].frequency == "Daily" &&
      azurerm_backup_policy_file_share.file_share_policy.backup[0].time == "23:00" &&
      azurerm_backup_policy_file_share.file_share_policy.retention_daily[0].count == 14 &&
      azurerm_backup_policy_file_share.file_share_policy.retention_weekly[0].count == 4 &&
      contains(azurerm_backup_policy_file_share.file_share_policy.retention_weekly[0].weekdays, "Sunday") &&
      azurerm_backup_policy_file_share.file_share_policy.retention_monthly[0].count == 12 &&
      contains(azurerm_backup_policy_file_share.file_share_policy.retention_monthly[0].weekdays, "Monday") &&
      contains(azurerm_backup_policy_file_share.file_share_policy.retention_monthly[0].weeks, "First") &&
      azurerm_backup_policy_file_share.file_share_policy.retention_yearly[0].count == 2 &&
      contains(azurerm_backup_policy_file_share.file_share_policy.retention_yearly[0].weekdays, "Sunday") &&
      contains(azurerm_backup_policy_file_share.file_share_policy.retention_yearly[0].weeks, "Last") &&
      contains(azurerm_backup_policy_file_share.file_share_policy.retention_yearly[0].months, "December")
    )
    error_message = "The file-share policy must preserve the original defaults."
  }
}

run "custom_policies" {
  command = plan

  module {
    source = "./backup"
  }

  variables {
    vm_backup_policy = {
      timezone         = "GMT Standard Time"
      time             = "21:30"
      daily_count      = 30
      weekly_count     = 0
      monthly_count    = 6
      monthly_weekdays = ["Saturday"]
      monthly_weeks    = ["Second"]
      yearly_count     = 0
    }
    fileshare_backup_policy = {
      time            = "23:30"
      daily_count     = 21
      weekly_count    = 8
      weekly_weekdays = ["Tuesday", "Friday"]
      monthly_count   = 0
      yearly_count    = 3
      yearly_weekdays = ["Thursday"]
      yearly_weeks    = ["Fourth"]
      yearly_months   = ["June"]
    }
  }

  assert {
    condition = (
      azurerm_backup_policy_vm.vm_policy.timezone == "GMT Standard Time" &&
      azurerm_backup_policy_vm.vm_policy.backup[0].time == "21:30" &&
      azurerm_backup_policy_vm.vm_policy.retention_daily[0].count == 30 &&
      length(azurerm_backup_policy_vm.vm_policy.retention_weekly) == 0 &&
      azurerm_backup_policy_vm.vm_policy.retention_monthly[0].count == 6 &&
      contains(azurerm_backup_policy_vm.vm_policy.retention_monthly[0].weekdays, "Saturday") &&
      contains(azurerm_backup_policy_vm.vm_policy.retention_monthly[0].weeks, "Second") &&
      length(azurerm_backup_policy_vm.vm_policy.retention_yearly) == 0
    )
    error_message = "The VM policy must apply its independent overrides."
  }

  assert {
    condition = (
      azurerm_backup_policy_file_share.file_share_policy.timezone == "UTC" &&
      azurerm_backup_policy_file_share.file_share_policy.backup[0].time == "23:30" &&
      azurerm_backup_policy_file_share.file_share_policy.retention_daily[0].count == 21 &&
      azurerm_backup_policy_file_share.file_share_policy.retention_weekly[0].count == 8 &&
      contains(azurerm_backup_policy_file_share.file_share_policy.retention_weekly[0].weekdays, "Tuesday") &&
      contains(azurerm_backup_policy_file_share.file_share_policy.retention_weekly[0].weekdays, "Friday") &&
      length(azurerm_backup_policy_file_share.file_share_policy.retention_monthly) == 0 &&
      azurerm_backup_policy_file_share.file_share_policy.retention_yearly[0].count == 3 &&
      contains(azurerm_backup_policy_file_share.file_share_policy.retention_yearly[0].weekdays, "Thursday") &&
      contains(azurerm_backup_policy_file_share.file_share_policy.retention_yearly[0].weeks, "Fourth") &&
      contains(azurerm_backup_policy_file_share.file_share_policy.retention_yearly[0].months, "June")
    )
    error_message = "The file-share policy must apply its independent overrides."
  }
}
