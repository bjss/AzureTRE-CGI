locals {
  core_vnet                       = "vnet-${var.tre_id}"
  core_resource_group_name        = "rg-${var.tre_id}"
  nexus_allowed_fqdns             = "pypi.org,*.pypi.org,files.pythonhosted.org,security.ubuntu.com,archive.ubuntu.com,keyserver.ubuntu.com,repo.anaconda.com,*.docker.com,*.docker.io,conda.anaconda.org,azure.archive.ubuntu.com,packages.microsoft.com,repo.almalinux.org,download-ib01.fedoraproject.org,cran.r-project.org,cloud.r-project.org,download1.rstudio.org,*.snapcraftcontent.com,download.microsoft.com,marketplace.visualstudio.com,*.gallery.vsassets.io,*.gallerycdn.vsassets.io,azcliprod.blob.core.windows.net,update.code.visualstudio.com,vscode.download.prss.microsoft.com,github.com,objects.githubusercontent.com,release-assets.githubusercontent.com"
  nexus_allowed_fqdns_list        = distinct(compact(split(",", replace(local.nexus_allowed_fqdns, " ", ""))))
  workspace_vm_allowed_fqdns      = "*.o.lencr.org,*.c.lencr.org,*.i.lencr.org"
  workspace_vm_allowed_fqdns_list = distinct(compact(split(",", replace(local.workspace_vm_allowed_fqdns, " ", ""))))
  storage_account_name            = lower(replace("stg-${var.tre_id}", "-", ""))
  tre_shared_service_tags = {
    tre_id                = var.tre_id
    tre_shared_service_id = var.tre_resource_id
  }
  cmk_name                 = "tre-encryption-${var.tre_id}"
  encryption_identity_name = "id-encryption-${var.tre_id}"
  # The bundle build pushes this image to the management ACR. See ../docker/Dockerfile.
  nexus_image_version = replace(replace(replace(file("${path.module}/../docker/version.txt"), "__version__ = \"", ""), "\"", ""), "\n", "")
  nexus_image         = "${data.azurerm_container_registry.mgmt_acr.login_server}/microsoft/azuretre/nexus3:${local.nexus_image_version}"
}
