from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class InstanceSummary:
    id: str
    display_name: str
    lifecycle_state: str
    availability_domain: str | None = None
    shape: str | None = None
    public_ip: str | None = None
    private_ip: str | None = None
    region: str | None = None


class OCIService:
    def __init__(self, config_file: Path, profile: str = "DEFAULT"):
        self.config_file = Path(config_file)
        self.profile = profile

    def _clients(self):
        import oci

        config = oci.config.from_file(str(self.config_file), self.profile)
        compute = oci.core.ComputeClient(config)
        virtual_network = oci.core.VirtualNetworkClient(config)
        identity = oci.identity.IdentityClient(config)
        return oci, config, compute, virtual_network, identity

    def list_compartments(self) -> list[dict[str, Any]]:
        oci, config, _compute, _vcn, identity = self._clients()
        tenancy_id = config["tenancy"]
        response = oci.pagination.list_call_get_all_results(
            identity.list_compartments,
            tenancy_id,
            compartment_id_in_subtree=True,
            access_level="ACCESSIBLE",
        )
        compartments = [{"id": tenancy_id, "name": "root"}]
        compartments.extend(
            {"id": item.id, "name": item.name}
            for item in response.data
            if getattr(item, "lifecycle_state", None) == "ACTIVE"
        )
        return compartments

    def list_instances(self) -> list[InstanceSummary]:
        oci, config, compute, vcn, _identity = self._clients()
        instances: list[InstanceSummary] = []
        for compartment in self.list_compartments():
            response = oci.pagination.list_call_get_all_results(
                compute.list_instances,
                compartment["id"],
            )
            for item in response.data:
                public_ip, private_ip = self._lookup_primary_ip(compute, vcn, compartment["id"], item.id)
                instances.append(
                    InstanceSummary(
                        id=item.id,
                        display_name=item.display_name,
                        lifecycle_state=item.lifecycle_state,
                        availability_domain=item.availability_domain,
                        shape=item.shape,
                        public_ip=public_ip,
                        private_ip=private_ip,
                        region=config.get("region"),
                    )
                )
        return instances

    def _lookup_primary_ip(self, compute, vcn, compartment_id: str, instance_id: str) -> tuple[str | None, str | None]:
        try:
            attachments = compute.list_vnic_attachments(
                compartment_id=compartment_id,
                instance_id=instance_id,
            ).data
            if not attachments:
                return None, None
            vnic = vcn.get_vnic(attachments[0].vnic_id).data
            return vnic.public_ip, vnic.private_ip
        except Exception:
            return None, None

    def list_availability_domains(self) -> list[str]:
        _oci, config, _compute, _vcn, identity = self._clients()
        response = identity.list_availability_domains(config["tenancy"])
        return [item.name for item in response.data]

    def launch_instance(self, template: dict[str, Any]) -> InstanceSummary:
        oci, _config, compute, _vcn, _identity = self._clients()
        required = ["compartment_id", "availability_domain", "subnet_id", "image_id", "shape"]
        missing = [key for key in required if not template.get(key)]
        if missing:
            raise ValueError("抢机模板缺少字段: " + ", ".join(missing))

        metadata = dict(template.get("metadata") or {})
        if template.get("ssh_authorized_keys"):
            metadata["ssh_authorized_keys"] = template["ssh_authorized_keys"]

        shape_config = None
        if template.get("shape_config"):
            shape_config = oci.core.models.LaunchInstanceShapeConfigDetails(**template["shape_config"])

        details = oci.core.models.LaunchInstanceDetails(
            compartment_id=template["compartment_id"],
            availability_domain=template["availability_domain"],
            display_name=template.get("display_name") or "oci-sniper-instance",
            shape=template["shape"],
            shape_config=shape_config,
            source_details=oci.core.models.InstanceSourceViaImageDetails(
                source_type="image",
                image_id=template["image_id"],
                boot_volume_size_in_gbs=template.get("boot_volume_size_in_gbs"),
            ),
            create_vnic_details=oci.core.models.CreateVnicDetails(
                subnet_id=template["subnet_id"],
                assign_public_ip=bool(template.get("assign_public_ip", True)),
                display_name=template.get("vnic_display_name"),
                hostname_label=template.get("hostname_label"),
            ),
            metadata=metadata or None,
        )
        response = compute.launch_instance(details)
        item = response.data
        return InstanceSummary(
            id=item.id,
            display_name=item.display_name,
            lifecycle_state=item.lifecycle_state,
            availability_domain=item.availability_domain,
            shape=item.shape,
        )

    def instance_action(self, instance_id: str, action: str) -> str:
        _oci, _config, compute, _vcn, _identity = self._clients()
        normalized = action.upper()
        if normalized not in {"START", "STOP", "SOFTSTOP", "RESET", "SOFTRESET"}:
            raise ValueError(f"Unsupported OCI action: {action}")
        response = compute.instance_action(instance_id, normalized)
        return getattr(response.data, "lifecycle_state", "accepted")
