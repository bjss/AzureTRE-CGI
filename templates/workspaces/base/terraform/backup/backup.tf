
resource "azurerm_recovery_services_vault" "vault" {
  name                = local.vault_name
  location            = var.location
  resource_group_name = var.resource_group_name
  sku                 = "Standard"
  storage_mode_type   = "ZoneRedundant" #  Possible values are "GeoRedundant", "LocallyRedundant" and "ZoneRedundant". Defaults to "GeoRedundant".
  tags                = var.tre_workspace_tags

  dynamic "identity" {
    for_each = var.enable_cmk_encryption ? [1] : []
    content {
      type         = "UserAssigned"
      identity_ids = [azurerm_user_assigned_identity.encryption_identity[0].id]
    }
  }

  dynamic "encryption" {
    for_each = var.enable_cmk_encryption ? [1] : []
    content {
      key_id                            = azurerm_key_vault_key.encryption_key[0].versionless_id
      infrastructure_encryption_enabled = true
      user_assigned_identity_id         = azurerm_user_assigned_identity.encryption_identity[0].id
      use_system_assigned_identity      = false
    }
  }

  lifecycle { ignore_changes = [encryption, tags] }

}

resource "azurerm_backup_policy_vm" "vm_policy" {
  name                = local.vm_backup_policy_name
  resource_group_name = var.resource_group_name
  recovery_vault_name = azurerm_recovery_services_vault.vault.name


  timezone = var.vm_backup_policy.timezone

  backup {
    frequency = "Daily"
    time      = var.vm_backup_policy.time
  }

  retention_daily {
    count = var.vm_backup_policy.daily_count
  }

  dynamic "retention_weekly" {
    for_each = var.vm_backup_policy.weekly_count > 0 ? [1] : []
    content {
      count    = var.vm_backup_policy.weekly_count
      weekdays = var.vm_backup_policy.weekly_weekdays
    }
  }

  dynamic "retention_monthly" {
    for_each = var.vm_backup_policy.monthly_count > 0 ? [1] : []
    content {
      count    = var.vm_backup_policy.monthly_count
      weekdays = var.vm_backup_policy.monthly_weekdays
      weeks    = var.vm_backup_policy.monthly_weeks
    }
  }

  dynamic "retention_yearly" {
    for_each = var.vm_backup_policy.yearly_count > 0 ? [1] : []
    content {
      count    = var.vm_backup_policy.yearly_count
      months   = var.vm_backup_policy.yearly_months
      weekdays = var.vm_backup_policy.yearly_weekdays
      weeks    = var.vm_backup_policy.yearly_weeks
    }
  }

  depends_on = [
    azurerm_recovery_services_vault.vault
  ]


}

resource "azurerm_backup_policy_file_share" "file_share_policy" {
  name                = local.fs_backup_policy_name
  resource_group_name = var.resource_group_name
  recovery_vault_name = azurerm_recovery_services_vault.vault.name

  timezone = var.fileshare_backup_policy.timezone

  backup {
    frequency = "Daily"
    time      = var.fileshare_backup_policy.time
  }

  retention_daily {
    count = var.fileshare_backup_policy.daily_count
  }

  dynamic "retention_weekly" {
    for_each = var.fileshare_backup_policy.weekly_count > 0 ? [1] : []
    content {
      count    = var.fileshare_backup_policy.weekly_count
      weekdays = var.fileshare_backup_policy.weekly_weekdays
    }
  }

  dynamic "retention_monthly" {
    for_each = var.fileshare_backup_policy.monthly_count > 0 ? [1] : []
    content {
      count    = var.fileshare_backup_policy.monthly_count
      weekdays = var.fileshare_backup_policy.monthly_weekdays
      weeks    = var.fileshare_backup_policy.monthly_weeks
    }
  }

  dynamic "retention_yearly" {
    for_each = var.fileshare_backup_policy.yearly_count > 0 ? [1] : []
    content {
      count    = var.fileshare_backup_policy.yearly_count
      months   = var.fileshare_backup_policy.yearly_months
      weekdays = var.fileshare_backup_policy.yearly_weekdays
      weeks    = var.fileshare_backup_policy.yearly_weeks
    }
  }

  depends_on = [
    azurerm_recovery_services_vault.vault
  ]

}
