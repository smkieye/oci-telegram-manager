from __future__ import annotations

import base64
import secrets
import string
import time
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
    cpu: float | int | None = None
    memory_gb: float | int | None = None
    disk_gb: float | int | None = None
    arch: str | None = None


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
                shape_config = getattr(item, "shape_config", None)
                cpu = getattr(shape_config, "ocpus", None) if shape_config else None
                memory_gb = getattr(shape_config, "memory_in_gbs", None) if shape_config else None
                disk_gb = self._lookup_boot_volume_size_gb(
                    oci,
                    config,
                    compartment["id"],
                    item.availability_domain,
                    item.id,
                )
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
                        cpu=cpu,
                        memory_gb=memory_gb,
                        disk_gb=disk_gb,
                        arch=self._instance_arch(item.shape),
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

    def _lookup_boot_volume_size_gb(self, oci, config, compartment_id: str, availability_domain: str | None, instance_id: str) -> int | None:
        if not availability_domain:
            return None
        try:
            compute = oci.core.ComputeClient(config)
            block = oci.core.BlockstorageClient(config)
            attachments = compute.list_boot_volume_attachments(
                availability_domain=availability_domain,
                compartment_id=compartment_id,
                instance_id=instance_id,
            ).data
            if not attachments:
                return None
            boot_volume = block.get_boot_volume(attachments[0].boot_volume_id).data
            return getattr(boot_volume, "size_in_gbs", None)
        except Exception:
            return None

    @staticmethod
    def _instance_arch(shape: str | None) -> str | None:
        if not shape:
            return None
        text = shape.lower()
        if "a1" in text or "arm" in text:
            return "ARM"
        if "e2" in text or "e3" in text or "e4" in text or "amd" in text:
            return "AMD"
        if "intel" in text:
            return "INTEL"
        return None

    def list_availability_domains(self) -> list[str]:
        _oci, config, _compute, _vcn, identity = self._clients()
        response = identity.list_availability_domains(config["tenancy"])
        return [item.name for item in response.data]

    @staticmethod
    def generate_root_password(length: int = 16) -> str:
        alphabet = string.ascii_letters + string.digits + "#%*-_=+"
        return "".join(secrets.choice(alphabet) for _ in range(length))

    def _first_accessible_compartment_id(self, preferred: str | None = None) -> str:
        if preferred:
            return preferred
        return self.list_compartments()[0]["id"]

    def _first_subnet_id(self, vcn, compartment_id: str, preferred: str | None = None) -> str:
        if preferred:
            return preferred
        compartments = [compartment_id]
        if compartment_id == self._clients()[1]["tenancy"]:
            compartments = [item["id"] for item in self.list_compartments()]
        for cid in compartments:
            try:
                subnets = vcn.list_subnets(cid).data
            except Exception:
                continue
            for subnet in subnets:
                if getattr(subnet, "lifecycle_state", None) == "AVAILABLE":
                    return subnet.id
        raise ValueError("没有找到可用 Subnet。请先在 OCI 创建 VCN/Subnet，或在模板里填写 subnet_id。")

    def _latest_image_id(self, compute, compartment_id: str, arch: str, os_type: str, preferred: str | None = None) -> str:
        if preferred:
            return preferred
        operating_system = "Oracle Autonomous Linux" if os_type == "oracle" else "Canonical Ubuntu"
        images = compute.list_images(compartment_id=compartment_id, operating_system=operating_system).data
        arch_keywords = ["aarch64", "arm"] if arch == "arm" else ["x86_64", "amd64"]
        filtered = []
        for image in images:
            text = f"{getattr(image, 'display_name', '')} {getattr(image, 'operating_system_version', '')}".lower()
            if any(word in text for word in arch_keywords):
                filtered.append(image)
        candidates = filtered or images
        if not candidates:
            raise ValueError(f"没有找到系统镜像：{operating_system} / {arch.upper()}")
        candidates.sort(key=lambda item: str(getattr(item, "time_created", "")), reverse=True)
        return candidates[0].id

    @staticmethod
    def _root_password_user_data(password: str) -> str:
        yaml_password = "'" + password.replace("'", "''") + "'"
        script = f"""#cloud-config
ssh_pwauth: true
disable_root: false
chpasswd:
  expire: false
  users:
    - name: root
      password: {yaml_password}
      type: text
runcmd:
  - sed -i 's/^#*PermitRootLogin .*/PermitRootLogin yes/' /etc/ssh/sshd_config
  - sed -i 's/^#*PasswordAuthentication .*/PasswordAuthentication yes/' /etc/ssh/sshd_config
  - systemctl restart ssh || systemctl restart sshd
"""
        return base64.b64encode(script.encode("utf-8")).decode("ascii")

    def normalize_sniper_template(self, template: dict[str, Any]) -> dict[str, Any]:
        oci, config, _compute, vcn, _identity = self._clients()
        normalized = dict(template)
        arch = str(normalized.get("arch", "arm")).lower()
        if arch not in {"arm", "amd"}:
            raise ValueError("系统架构只能是 ARM 或 AMD")
        normalized["arch"] = arch
        normalized["os_type"] = str(normalized.get("os_type", "ubuntu")).lower()
        normalized["cpu"] = int(normalized.get("cpu", 1))
        normalized["memory_gb"] = int(normalized.get("memory_gb", 6 if arch == "arm" else 1))
        normalized["disk_gb"] = int(normalized.get("disk_gb", 50))
        normalized["count"] = int(normalized.get("count", 1))
        normalized["interval_seconds"] = int(normalized.get("interval_seconds", 60))
        normalized["shape"] = normalized.get("shape") or ("VM.Standard.A1.Flex" if arch == "arm" else "VM.Standard.E2.1.Micro")
        normalized["compartment_id"] = self._first_accessible_compartment_id(normalized.get("compartment_id"))
        ads = self.list_availability_domains()
        normalized["availability_domain"] = normalized.get("availability_domain") or (ads[0] if ads else None)
        if not normalized["availability_domain"]:
            raise ValueError("没有找到可用 AD")
        normalized["subnet_id"] = self._first_subnet_id(vcn, normalized["compartment_id"], normalized.get("subnet_id"))
        normalized["display_name"] = normalized.get("display_name") or f"free-{arch}"
        normalized["assign_public_ip"] = bool(normalized.get("assign_public_ip", True))
        root_password = str(normalized.get("root_password") or "").strip()
        if not root_password or root_password.lower() == "random":
            root_password = self.generate_root_password()
        normalized["root_password"] = root_password
        return normalized

    def launch_instance(self, template: dict[str, Any]) -> InstanceSummary:
        oci, config, compute, vcn, _identity = self._clients()
        template = self.normalize_sniper_template(template)
        template["image_id"] = self._latest_image_id(
            compute,
            template["compartment_id"],
            template["arch"],
            template.get("os_type", "ubuntu"),
            template.get("image_id"),
        )

        metadata = dict(template.get("metadata") or {})
        metadata["user_data"] = self._root_password_user_data(template["root_password"])
        if template.get("ssh_authorized_keys"):
            metadata["ssh_authorized_keys"] = template["ssh_authorized_keys"]

        shape_config = None
        if template["shape"] == "VM.Standard.A1.Flex" or template.get("shape_config"):
            shape_config_values = {
                "ocpus": template.get("cpu", 1),
                "memory_in_gbs": template.get("memory_gb", 6),
            }
            shape_config_values.update(dict(template.get("shape_config") or {}))
            shape_config = oci.core.models.LaunchInstanceShapeConfigDetails(**shape_config_values)

        details = oci.core.models.LaunchInstanceDetails(
            compartment_id=template["compartment_id"],
            availability_domain=template["availability_domain"],
            display_name=template["display_name"],
            shape=template["shape"],
            shape_config=shape_config,
            source_details=oci.core.models.InstanceSourceViaImageDetails(
                source_type="image",
                image_id=template["image_id"],
                boot_volume_size_in_gbs=template.get("disk_gb"),
            ),
            create_vnic_details=oci.core.models.CreateVnicDetails(
                subnet_id=template["subnet_id"],
                assign_public_ip=template["assign_public_ip"],
                display_name=template.get("vnic_display_name"),
                hostname_label=template.get("hostname_label"),
            ),
            metadata=metadata,
        )
        response = compute.launch_instance(details)
        item = response.data
        public_ip = None
        private_ip = None
        # VNIC attachment / public IP can lag behind launch_instance. Poll briefly so
        # the Telegram success message can include the new public IP when OCI has it ready.
        for _ in range(10):
            public_ip, private_ip = self._lookup_primary_ip(compute, vcn, template["compartment_id"], item.id)
            if public_ip or not template.get("assign_public_ip", True):
                break
            time.sleep(3)
        return InstanceSummary(
            id=item.id,
            display_name=item.display_name,
            lifecycle_state=item.lifecycle_state,
            availability_domain=item.availability_domain,
            shape=item.shape,
            public_ip=public_ip,
            private_ip=private_ip,
            region=config.get("region"),
        )

    def instance_action(self, instance_id: str, action: str) -> str:
        _oci, _config, compute, _vcn, _identity = self._clients()
        normalized = action.upper()
        if normalized not in {"START", "STOP", "SOFTSTOP", "RESET", "SOFTRESET"}:
            raise ValueError(f"Unsupported OCI action: {action}")
        response = compute.instance_action(instance_id, normalized)
        return getattr(response.data, "lifecycle_state", "accepted")
