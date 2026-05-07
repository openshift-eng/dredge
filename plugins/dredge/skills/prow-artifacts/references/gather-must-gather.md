# gather-must-gather

OpenShift must-gather is a cluster diagnostic snapshot collected by `oc adm must-gather`. A typical collection is ~200MB and ~5500 files. It captures the state of the cluster at a single point in time.

## Location

After download, artifacts are at:

```
<step_dir>/artifacts/must-gather/
```

For example: `.dredge/<build_id>/e2e-aws-capi-techpreview/gather-must-gather/artifacts/must-gather/`

## Structure

### Root level

The must-gather root contains:

- `must-gather.log` / `must-gather.logs` — collection output logs
- `event-filter.html` — HTML event filtering/visualization tool
- `camgi.html` — HTML cluster analysis tool
- `timestamp` — collection start/end timestamps

### Data directory

The actual cluster data is nested one level deeper under a hash-named directory (the must-gather image reference). Everything below is relative to that directory.

Use `ls` or `find -maxdepth 1` on the must-gather root to discover the hash directory name.

### cluster-scoped-resources/

Cluster-wide Kubernetes and OpenShift resource manifests organized by API group. Each API group is a subdirectory containing YAML files.

Key subdirectories:

| Directory | Contents |
|---|---|
| `config.openshift.io/` | Cluster configuration: clusterversions, infrastructure, networks, oauth, images, dns, proxies |
| `core/` | Nodes, namespaces, persistent volumes |
| `machineconfiguration.openshift.io/` | MachineConfigs, MachineConfigPools |
| `machine.openshift.io/` | Machine and MachineSet objects |
| `infrastructure.cluster.x-k8s.io/` | Cluster API infrastructure resources |
| `operator.openshift.io/` | Operator custom resources |
| `rbac.authorization.k8s.io/` | ClusterRoles, ClusterRoleBindings |
| `storage.k8s.io/` | StorageClasses, CSI drivers |
| `apiextensions.k8s.io/` | CustomResourceDefinitions |
| `security.openshift.io/` | SecurityContextConstraints |

### namespaces/

Per-namespace resource dumps. This is the largest section (~150MB). Contains 70+ namespace directories, each with subdirectories for resource types (pods, deployments, configmaps, services, etc.) as YAML files.

Key namespaces:

| Namespace | What you'll find |
|---|---|
| `openshift-kube-apiserver/` | API server pods, configs, logs |
| `openshift-kube-controller-manager/` | Controller manager state |
| `openshift-etcd/` | etcd operator and pod state |
| `openshift-machine-config-operator/` | Machine configuration operator |
| `openshift-cluster-api/` | Cluster API controllers |
| `openshift-cluster-api-operator/` | CAPI operator |
| `openshift-network-operator/` | Network operator and config |
| `openshift-ingress/` | Ingress controller pods |
| `openshift-monitoring/` | Prometheus, alertmanager |
| `openshift-cloud-controller-manager/` | Cloud provider integration |
| `openshift-cloud-credential-operator/` | Cloud credentials |
| `openshift-console/` | Console pods and config |

### nodes/

Per-node diagnostics. One subdirectory per node (named by hostname, e.g. `ip-10-0-26-33.ec2.internal`).

Each node directory contains:

| File | Contents |
|---|---|
| `sysinfo.log` | System information snapshot |
| `dmesg` | Kernel message buffer |
| `lscpu` | CPU information |
| `lspci` | PCI device listing |
| `proc_cmdline` | Kernel command line |
| `ethtool_features` / `ethtool_channels` | Network interface details |
| `cpu_affinities.json` | CPU affinity information |
| `irq_affinities.json` | IRQ affinity mappings |
| `podresources.json` | Pod resource allocation |
| `*_logs_kubelet.gz` | Compressed kubelet logs |

### etcd_info/

etcd cluster diagnostics.

| File | Contents |
|---|---|
| `object_count.json` | Count and size of all objects in etcd by type |
| `endpoint_health.json` | Health status of each etcd endpoint |
| `endpoint_status.json` | Detailed per-endpoint metrics |
| `member_list.json` | Cluster membership |
| `alarm_list.json` | Active alarms |

### monitoring/

| Path | Contents |
|---|---|
| `prometheus/rules.json` | Prometheus alert rules |
| `prometheus/alertmanagers.json` | Alertmanager configuration |
| `alertmanager/status.json` | Current alert routing and status |

### host_service_logs/

systemd service logs from master nodes. Located under `masters/`.

Key logs: `crio_service.log`, `kubelet_service.log`, `openvswitch_service.log`, `ovsdb-server_service.log`, `NetworkManager_service.log`, `machine-config-daemon-*_service.log`

### network_logs/

CNI and OVN networking diagnostics: IP pools, network attachment definitions, network policies, OVN database backups (`ovnk_database_store.tar.gz`).

### Other directories

| Directory | Contents |
|---|---|
| `static-pods/` | Static pod definitions and termination logs |
| `pod_network_connectivity_check/` | Pod-to-pod connectivity test results |
| `ingress_controllers/` | Ingress controller state |
| `insights-data/` | Red Hat Insights telemetry |
| `istio/` | Service mesh data and API discovery |

## Investigation starting points

**Cluster version and health:**
Start with `cluster-scoped-resources/config.openshift.io/clusterversions.yaml`, then check `etcd_info/endpoint_health.json` and `monitoring/`.

**Node problems:**
Check `nodes/<hostname>/dmesg` and `nodes/<hostname>/sysinfo.log`. For kubelet issues, decompress `*_logs_kubelet.gz`. Check `host_service_logs/masters/` for crio and system service logs.

**Operator failures:**
Find the relevant `namespaces/openshift-<operator>/` directory. Look at pod YAML for status and container logs.

**Networking:**
Start with `network_logs/` and `pod_network_connectivity_check/`. For OVN specifics, check `namespaces/openshift-ovn-kubernetes/` if present.

**CAPI / machine issues:**
Check `cluster-scoped-resources/machine.openshift.io/` and `cluster-scoped-resources/infrastructure.cluster.x-k8s.io/`. For controller state, look at `namespaces/openshift-cluster-api/` and `namespaces/openshift-cluster-api-operator/`.

**etcd:**
Start with `etcd_info/endpoint_health.json` and `etcd_info/alarm_list.json`. Check `etcd_info/object_count.json` for database size concerns. For etcd pod state, check `namespaces/openshift-etcd/`.
