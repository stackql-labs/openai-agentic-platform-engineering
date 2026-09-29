// Normalise raw provider rows into one comparable attribute shape per resource type, so a delta
// between two snapshots is a string compare of attrs_json. Every normaliser returns a plain object;
// stable() serialises it with sorted keys.
//
// The aws provider returns the EC2 query API's XML transformed to JSON: list fields arrive as
// {"item": {...}} or {"item": [...]} with lowerCamel keys. PascalCase keys are accepted too.

export function parseJson(v) {
  if (typeof v === 'string') {
    const t = v.trim();
    if (t === '' || t === 'null') return null;
    if (t[0] === '[' || t[0] === '{') {
      try {
        return JSON.parse(t);
      } catch {
        return v;
      }
    }
  }
  return v;
}

export function items(v) {
  const p = parseJson(v);
  if (p && typeof p === 'object' && !Array.isArray(p) && 'item' in p) {
    const inner = p.item;
    return Array.isArray(inner) ? inner : [inner];
  }
  if (Array.isArray(p)) return p;
  return [];
}

function pick(obj, ...keys) {
  if (!obj || typeof obj !== 'object') return undefined;
  for (const k of keys) {
    if (obj[k] !== undefined && obj[k] !== null) return obj[k];
  }
  return undefined;
}

export function awsTags(v) {
  const out = {};
  for (const t of items(v)) {
    if (!t || typeof t !== 'object') continue;
    const k = pick(t, 'key', 'Key');
    const val = pick(t, 'value', 'Value');
    if (k !== undefined && !String(k).startsWith('aws:')) out[String(k)] = String(val ?? '');
  }
  return sortObject(out);
}

export function awsIngress(ipPermissions) {
  const rules = new Set();
  for (const perm of items(ipPermissions)) {
    if (!perm || typeof perm !== 'object') continue;
    const proto = String(pick(perm, 'ipProtocol', 'IpProtocol') ?? '-1');
    const from = String(pick(perm, 'fromPort', 'FromPort') ?? '');
    const to = String(pick(perm, 'toPort', 'ToPort') ?? '');
    const sources = [];
    for (const r of items(pick(perm, 'ipRanges', 'IpRanges'))) {
      const cidr = pick(r, 'cidrIp', 'CidrIp');
      if (cidr !== undefined) sources.push(String(cidr));
    }
    for (const r of items(pick(perm, 'ipv6Ranges', 'Ipv6Ranges'))) {
      const cidr = pick(r, 'cidrIpv6', 'CidrIpv6');
      if (cidr !== undefined) sources.push(String(cidr));
    }
    for (const g of items(pick(perm, 'groups', 'UserIdGroupPairs'))) {
      const gid = pick(g, 'groupId', 'GroupId');
      if (gid !== undefined) sources.push(String(gid));
    }
    for (const src of sources) rules.add(`${proto}:${from}-${to}:${src}`);
  }
  return [...rules].sort();
}

export function ec2Instance(row) {
  const state = parseJson(row.state);
  return {
    instance_type: row.instance_type ?? null,
    state: typeof state === 'object' && state ? (state.name ?? null) : (state ?? null),
    tags: awsTags(row.tags),
  };
}

export function securityGroup(row) {
  return {
    name: row.group_name ?? null,
    ingress: awsIngress(row.ip_permissions),
    tags: awsTags(row.tags),
  };
}

export function azureNsg(row) {
  const rules = [];
  for (const r of parseJson(row.security_rules) || []) {
    const p = r.properties || {};
    rules.push(
      `${r.name}:${p.direction}:${p.access}:${p.protocol}:${p.destinationPortRange}:${p.sourceAddressPrefix}:${p.priority}`
    );
  }
  const tags = parseJson(row.tags) || {};
  const tagOut = {};
  for (const [k, v] of Object.entries(tags)) tagOut[k] = String(v);
  return { name: row.name ?? null, rules: rules.sort(), tags: sortObject(tagOut) };
}

export function sortObject(obj) {
  if (Array.isArray(obj)) return obj.map(sortObject);
  if (obj && typeof obj === 'object') {
    const out = {};
    for (const k of Object.keys(obj).sort()) out[k] = sortObject(obj[k]);
    return out;
  }
  return obj;
}

// Compact JSON with keys sorted at every level: two equal attribute sets serialise identically.
export function stable(obj) {
  return JSON.stringify(sortObject(obj));
}

// query id suffix -> how its rows become snapshot rows
export const SOURCES = Object.freeze({
  snapshot_aws_security_groups: {
    provider: 'aws',
    resourceType: 'security_group',
    keyColumn: 'group_id',
    normalise: securityGroup,
  },
  snapshot_aws_instances: {
    provider: 'aws',
    resourceType: 'ec2_instance',
    keyColumn: 'instance_id',
    normalise: ec2Instance,
  },
  snapshot_azure_nsgs: {
    provider: 'azure',
    resourceType: 'network_security_group',
    keyColumn: 'id',
    normalise: azureNsg,
  },
});
