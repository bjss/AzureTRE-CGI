resource "azurerm_resource_group" "rg" {
  location = data.azurerm_resource_group.rg.location
  name     = local.resource_group_name
  tags = merge(
    local.tre_shared_service_tags,
    {
      project = "Azure Trusted Research Environment",
      source  = "https://github.com/microsoft/AzureTRE/"
    },
  )

  lifecycle { ignore_changes = [tags] }
}

resource "azurerm_databricks_workspace" "databricks" {
  name                                  = local.databricks_workspace_name
  resource_group_name                   = local.resource_group_name
  location                              = azurerm_resource_group.rg.location
  sku                                   = "premium"
  managed_resource_group_name           = local.managed_resource_group_name
  infrastructure_encryption_enabled     = true
  public_network_access_enabled         = false
  network_security_group_rules_required = "NoAzureDatabricksRules"
  default_storage_firewall_enabled      = true
  access_connector_id                   = azurerm_databricks_access_connector.connector.id
  tags                                  = local.tre_shared_service_tags

  lifecycle { ignore_changes = [tags] }

  custom_parameters {
    no_public_ip                                         = true
    public_subnet_name                                   = azurerm_subnet.host.name
    private_subnet_name                                  = azurerm_subnet.container.name
    virtual_network_id                                   = azurerm_virtual_network.ws.id
    public_subnet_network_security_group_association_id  = azurerm_subnet_network_security_group_association.host.id
    private_subnet_network_security_group_association_id = azurerm_subnet_network_security_group_association.container.id
  }

  depends_on = [
    azurerm_subnet_network_security_group_association.host,
    azurerm_subnet_network_security_group_association.container,
    terraform_data.managed_resource_group_cleanup
  ]
}

# Databricks deletes its managed resource group during the workspace delete. With the default storage firewall, Event Grid
# cannot get a SAS for the workspace storage account, and Databricks then leaves that account and its group behind.
# The workspace depends on this resource, so on destroy this step runs after the workspace delete and removes the group.
# It deletes only a group that is still managed by this workspace after the workspace is gone.
resource "terraform_data" "managed_resource_group_cleanup" {
  input = {
    subscription_id             = split("/", azurerm_resource_group.rg.id)[2]
    managed_resource_group_name = local.managed_resource_group_name
    workspace_id                = "${azurerm_resource_group.rg.id}/providers/Microsoft.Databricks/workspaces/${local.databricks_workspace_name}"
  }

  provisioner "local-exec" {
    when        = destroy
    interpreter = ["/bin/bash", "-c"]
    command     = <<EOT
set -euo pipefail
# Use the cloud and the credentials of the bundle, as the resource processor does.
az cloud set --name "$${AZURE_ENVIRONMENT:-AzureCloud}"
if [ "$${ARM_USE_MSI:-true}" = "true" ]; then
  az login --identity --output none
else
  az login --service-principal --username "$ARM_CLIENT_ID" --password "$ARM_CLIENT_SECRET" --tenant "$ARM_TENANT_ID" --output none
fi
az account set --subscription "${self.input.subscription_id}"
group="${self.input.managed_resource_group_name}"
workspace="${self.input.workspace_id}"
if az resource show --ids "$workspace" --output none 2>/dev/null; then
  echo "The Databricks workspace still exists, so its managed resource group stays"
  exit 0
fi
# A failed call stops the script here. Inside the test, set -e does not stop it.
exists=$(az group exists --name "$group")
if [ "$exists" != "true" ]; then
  echo "Databricks deleted its managed resource group"
  exit 0
fi
managed_by=$(az group show --name "$group" --query managedBy --output tsv)
if [ "$(echo "$managed_by" | tr '[:upper:]' '[:lower:]')" != "$(echo "$workspace" | tr '[:upper:]' '[:lower:]')" ]; then
  echo "The resource group $group is not managed by $workspace, so it stays" >&2
  exit 1
fi
echo "Databricks left its managed resource group $group, so delete it"
az group delete --name "$group" --yes
EOT
  }
}

// required to set default_storage_firewall_enabled = true
// https://learn.microsoft.com/en-us/azure/databricks/security/network/storage/firewall-support
//
resource "azurerm_databricks_access_connector" "connector" {
  name                = "${local.databricks_workspace_name}-access-connector"
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
  tags                = local.tre_shared_service_tags

  identity {
    type = "SystemAssigned"
  }

  lifecycle { ignore_changes = [tags] }
}

resource "azurerm_monitor_diagnostic_setting" "databricks_diagnostics" {
  name                       = "diagnostics-${local.databricks_workspace_name}"
  target_resource_id         = azurerm_databricks_workspace.databricks.id
  log_analytics_workspace_id = data.azurerm_log_analytics_workspace.tre.id

  enabled_log {
    category = "accounts"
  }

  enabled_log {
    category = "workspace"
  }

  enabled_log {
    category = "gitCredentials"
  }

  enabled_log {
    category = "iamRole"
  }

  enabled_log {
    category = "secrets"
  }
}
