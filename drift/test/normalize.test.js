import { test } from 'node:test';
import assert from 'node:assert/strict';
import { awsIngress, awsTags, azureNsg, ec2Instance, securityGroup, stable } from '../src/normalize.js';

test('awsIngress flattens the item-wrapped lowerCamel shape into sorted rule strings', () => {
  const ipPermissions = JSON.stringify({
    item: [
      { ipProtocol: 'tcp', fromPort: 443, toPort: 443, ipRanges: { item: { cidrIp: '10.0.0.0/8' } } },
      { ipProtocol: 'tcp', fromPort: 22, toPort: 22, ipRanges: { item: [{ cidrIp: '0.0.0.0/0' }] }, groups: { item: { groupId: 'sg-1' } } },
    ],
  });
  assert.deepEqual(awsIngress(ipPermissions), ['tcp:22-22:0.0.0.0/0', 'tcp:22-22:sg-1', 'tcp:443-443:10.0.0.0/8']);
});

test('awsIngress accepts the PascalCase list shape too', () => {
  const rules = awsIngress([{ IpProtocol: 'tcp', FromPort: 443, ToPort: 443, IpRanges: [{ CidrIp: '10.0.0.0/8' }] }]);
  assert.deepEqual(rules, ['tcp:443-443:10.0.0.0/8']);
  assert.deepEqual(awsIngress(null), []);
  assert.deepEqual(awsIngress('null'), []);
});

test('awsTags drops aws:* keys and sorts', () => {
  const tags = awsTags(JSON.stringify({ item: [{ key: 'purpose', value: 'agentic-demo' }, { key: 'aws:cloudformation:stack', value: 'x' }, { key: 'Name', value: 'app' }] }));
  assert.deepEqual(Object.keys(tags), ['Name', 'purpose']);
  assert.equal(tags.purpose, 'agentic-demo');
});

test('securityGroup and ec2Instance produce the comparable shape', () => {
  const sg = securityGroup({ group_id: 'sg-1', group_name: 'app-sg', ip_permissions: '{"item":{"ipProtocol":"tcp","fromPort":443,"toPort":443,"ipRanges":{"item":{"cidrIp":"10.0.0.0/8"}}}}', tags: null });
  assert.deepEqual(sg, { name: 'app-sg', ingress: ['tcp:443-443:10.0.0.0/8'], tags: {} });
  const inst = ec2Instance({ instance_id: 'i-1', instance_type: 't3.micro', state: '{"code":16,"name":"running"}', tags: '{"item":{"key":"purpose","value":"agentic-demo"}}' });
  assert.deepEqual(inst, { instance_type: 't3.micro', state: 'running', tags: { purpose: 'agentic-demo' } });
});

test('azureNsg flattens rules and tags', () => {
  const row = {
    id: '/subscriptions/x/resourceGroups/rg/providers/Microsoft.Network/networkSecurityGroups/nsg',
    name: 'nsg',
    security_rules: JSON.stringify([
      { name: 'AllowHttpsFromCorp', properties: { direction: 'Inbound', access: 'Allow', protocol: 'Tcp', destinationPortRange: '443', sourceAddressPrefix: '10.0.0.0/8', priority: 100 } },
      { name: 'AllowAnyInbound', properties: { direction: 'Inbound', access: 'Allow', protocol: '*', destinationPortRange: '*', sourceAddressPrefix: '*', priority: 110 } },
    ]),
    tags: { purpose: 'agentic-demo', 'managed-by': 'stackql-deploy' },
  };
  const n = azureNsg(row);
  assert.equal(n.name, 'nsg');
  assert.deepEqual(n.rules, ['AllowAnyInbound:Inbound:Allow:*:*:*:110', 'AllowHttpsFromCorp:Inbound:Allow:Tcp:443:10.0.0.0/8:100']);
  assert.deepEqual(Object.keys(n.tags), ['managed-by', 'purpose']);
});

test('stable serialises with sorted keys at every level so equal attrs compare equal', () => {
  const a = stable({ tags: { b: '1', a: '2' }, ingress: ['x'], name: 'n' });
  const b = stable({ name: 'n', ingress: ['x'], tags: { a: '2', b: '1' } });
  assert.equal(a, b);
  assert.equal(a, '{"ingress":["x"],"name":"n","tags":{"a":"2","b":"1"}}');
});
