# Azure TRE base workspace

The base workspace template is the foundation that all other workspaces and workspace services are built upon. Alternative workspace architectures could be used. However, the templates provided in this repository use the base workspace.

The base workspace template contains the following resources:

- Virtual Network
- Storage Account
- Key Vault
- VNet Peer to Core VNet
- Network Security Group
- App Service Plan

## Workspace Configuration

When deploying a workspace the following properties need to be configured.

### Required Properties

| Property | Options | Description |
| -------- | ------- | ----------- |
| `client_id` | Valid client ID of the Workspace App Registration. | The OpenID client ID which should be submitted to the OpenID service when necessary. This value is typically provided to you by the OpenID service when OpenID credentials are generated for your application. |
| `client_secret` | Valid client secret. | |

## Backup

When `enable_backup` is set (the default) the base workspace deploys a Recovery Services Vault into the workspace resource group and protects the workspace's shared storage (and any VMs configured for backup).

The updateable `vm_backup_policy` and `fileshare_backup_policy` workspace properties independently configure the two existing policies. Each is an object containing any of the following optional overrides. Omitting a property retains the original default; omitting an entire policy preserves existing behaviour on upgrade. Backup frequency remains daily.

| Property | Default | Description |
| -------- | ------- | ----------- |
| `timezone` | `UTC` | Azure timezone name, such as `GMT Standard Time`. |
| `time` | `22:00` for VMs; `23:00` for file shares | Daily backup time in the selected timezone, on the hour or half-hour. |
| `daily_count` | `14` | Daily recovery points to retain. |
| `weekly_count` | `4` | Weeks to retain; set to `0` to disable weekly retention. |
| `weekly_weekdays` | `["Sunday"]` | Weekdays to retain. |
| `monthly_count` | `12` | Months to retain; set to `0` to disable monthly retention. |
| `monthly_weekdays`, `monthly_weeks` | `["Monday"]`, `["First"]` | Weekdays and weeks of each month to retain. |
| `yearly_count` | `2` | Years to retain; set to `0` to disable yearly retention. |
| `yearly_weekdays`, `yearly_weeks`, `yearly_months` | `["Sunday"]`, `["Last"]`, `["December"]` | Weekdays, weeks and months to retain. |

For example, the following workspace properties configure the policies independently:

```json
{
  "enable_backup": true,
  "vm_backup_policy": {
    "time": "21:30",
    "daily_count": 30,
    "weekly_weekdays": ["Saturday", "Sunday"]
  },
  "fileshare_backup_policy": {
    "time": "23:30",
    "daily_count": 21,
    "yearly_count": 0
  }
}
```

Changing a policy affects all resources protected by that policy. Azure validates the timezone and policy combinations; in particular, Azure Files has a limit of 200 retained snapshots across its retention schedules.
Reducing retention or disabling a retention period can shorten recovery-point availability. Review the impact before updating an existing workspace. These settings have no effect when backups are disabled.

When updating a policy through the API, provide the complete policy override object: a PATCH replaces that object rather than merging its individual fields. Any omitted fields revert to their defaults.

| `delete_backups_on_uninstall` | Behaviour on workspace deletion |
| ----------------------------- | ------------------------------- |
| `false` (default) | **Retain the backups.** Protection is stopped but the recovery points are kept. The vault, its backup resources and the workspace resource group are removed from the Terraform state, so the **resource group is retained and continues to hold the Recovery Services Vault and its recovery points** after the workspace is deleted. |
| `true` | **Delete the backups.** Protection is stopped with data deletion, the backup containers are unregistered and the Recovery Services Vault (with its recovery points) is deleted. Terraform then destroys the rest of the workspace, including its resource group, leaving nothing behind. |

Both flags are updateable, so the choice can be changed on an existing workspace before it is deleted.

> **Note:** If [vault immutability](https://learn.microsoft.com/azure/backup/backup-azure-immutable-vault-concept) is enabled on the Recovery Services Vault (for example, enforced by an Azure Policy in your environment), backups cannot be deleted before they expire. In this case `delete_backups_on_uninstall = true` will not be able to remove the backups or vault, and workspace deletion may fail.

## Azure Trusted Services
*Azure Trusted Services* are allowed to connect to both the key vault and storage account provisioned within the workspace. If this is undesirable additional resources without this setting configured could be deployed by a custom workspace.

Further details around which Azure services are allowed to connect can be found below:

- Key Vault: <https://docs.microsoft.com/en-us/azure/key-vault/general/overview-vnet-service-endpoints#trusted-services>
- Azure Storage: <https://docs.microsoft.com/en-us/azure/storage/common/storage-network-security?msclkid=ee4e79e4b97911eca46dae54da464d11&tabs=azure-portal#trusted-access-for-resources-registered-in-your-subscription>
