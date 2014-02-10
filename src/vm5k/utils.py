# Copyright 2009-2012 INRIA Rhone-Alpes, Service Experimentation et
# Developpement
#
# This file is part of Execo.
#
# Execo is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Execo is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
# or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public
# License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Execo.  If not, see <http://www.gnu.org/licenses/>
from xml.dom import minidom
from random import randint
from execo import logger, SshProcess, SequentialActions, Host, Local, sleep, default_connection_params
from execo.action import ActionFactory
from execo.log import style
from execo_g5k import get_oar_job_nodes, get_oargrid_job_oar_jobs, get_oar_job_subnets, \
    get_oar_job_kavlan, deploy, Deployment, wait_oar_job_start, wait_oargrid_job_start, \
    distribute_hosts
from execo_g5k.api_utils import get_host_cluster, get_g5k_sites, get_g5k_clusters, get_cluster_site, \
    get_host_attributes, get_resource_attributes, get_host_site, canonical_host_name

from xml.etree.ElementTree import Element, SubElement, tostring, parse


def hosts_list(hosts, separator=' '):
    """Return a string """
    for i, host in enumerate(hosts):
        if isinstance(host, Host):
            hosts[i] = host.address
            
    return separator.join([ style.host(host.split('.')[0]) for host in sorted(hosts)])
    

def get_oar_job_vm5k_resources( jobs ):
    """Retrieve the hosts list and (ip, mac) list from a list of oar_job and
    return the resources dict needed by vm5k_deployment """
    resources = {}
    for oar_job_id, site in jobs:
        logger.info('Retrieving resources from %s:%s', style.emph(site), oar_job_id)
        oar_job_id = int(oar_job_id)
        wait_oar_job_start(oar_job_id, site)
        logger.debug('Retrieving hosts')
        hosts = [host.address for host in get_oar_job_nodes(oar_job_id, site) ]
        logger.debug('Retrieving subnet')
        ip_mac, _ = get_oar_job_subnets( oar_job_id, site )
        kavlan = None
        if len(ip_mac) == 0:
            logger.debug('Retrieving kavlan')
            kavlan = get_oar_job_kavlan(oar_job_id, site)
            if kavlan:
                ip_mac = get_kavlan_ip_mac(kavlan, site)
        if 'grid5000.fr' in site:
            site = site.split('.')[0]
        resources[site] = {'hosts': hosts,'ip_mac': ip_mac, 'kavlan': kavlan}
    return resources

def get_oargrid_job_vm5k_resources(oargrid_job_id):
    """Retrieve the hosts list and (ip, mac) list by sites from an oargrid_job_id and
    return the resources dict needed by vm5k_deployment, with kavlan-global if used in
    the oargrid job """
    oargrid_job_id = int(oargrid_job_id) 
    logger.info('Waiting job start')
    wait_oargrid_job_start(oargrid_job_id)
    resources = get_oar_job_vm5k_resources( [ (oar_job_id, site) for oar_job_id, site in get_oargrid_job_oar_jobs(oargrid_job_id) ])
    kavlan_global = None
    for site, res in resources.iteritems():
        if res['kavlan'] >= 10:
            kavlan_global = {'kavlan': res['kavlan'], 'ip_mac': resources[site]['ip_mac'], 'site': site }
            break
    if kavlan_global:
        resources['global'] = kavlan_global

    return resources


def get_kavlan_network(kavlan, site):
    """Retrieve the network parameters for a given kavlan from the API"""
    network, mask_size = None, None
    equips = get_resource_attributes('/sites/'+site+'/network_equipments/')
    for equip in equips['items']:
        if equip.has_key('vlans') and len(equip['vlans']) >2:
            all_vlans = equip['vlans']
    for info in all_vlans.itervalues():
        if type(info) == type({}) and info.has_key('name') and info['name'] == 'kavlan-'+str(kavlan):
            network, _, mask_size = info['addresses'][0].partition('/',)
    logger.debug('network=%s, mask_size=%s', network, mask_size)
    return network, mask_size

def get_kavlan_ip_mac(kavlan, site):
    """Retrieve the network parameters for a given kavlan from the API"""
    network, mask_size = get_kavlan_network(kavlan, site)
    min_2 = (kavlan-4)*64 + 2 if kavlan < 8 else (kavlan-8)*64 + 2 if kavlan < 10 else 216
    ips = [ ".".join( [ str(part) for part in ip ]) for ip in [ ip for ip in get_ipv4_range(tuple([ int(part) for part in network.split('.') ]), int(mask_size))
           if ip[3] not in [ 0, 254, 255 ] and ip[2] >= min_2] ]
    macs = []
    for i in range(len(ips)):
        mac = ':'.join( map(lambda x: "%02x" % x, [ 0x00, 0x020, 0x4e,
            randint(0x00, 0xff),
            randint(0x00, 0xff),
            randint(0x00, 0xff) ] ))
        while mac in macs:
            mac = ':'.join( map(lambda x: "%02x" % x, [ 0x00, 0x020, 0x4e,
                randint(0x00, 0xff),
                randint(0x00, 0xff),
                randint(0x00, 0xff) ] ))
        macs.append(mac)
    return zip(ips, macs)


def get_ipv4_range(network, mask_size):
    net = ( network[0] << 24
            | network[1] << 16
            | network[2] << 8
            | network[3] )
    mask = ~(2**(32-mask_size)-1)
    ip_start = net & mask
    ip_end = net | ~mask
    return [ ((ip & 0xff000000) >> 24,
              (ip & 0xff0000) >> 16,
              (ip & 0xff00) >> 8,
              ip & 0xff)
             for ip in xrange(ip_start, ip_end + 1) ]
    
def print_step(step_desc = None):
    """ """
    logger.info(style.step(' '+step_desc+' ').center(50) )


def prettify(elem):
    """Return a pretty-printed XML string for the Element.  """
    rough_string = tostring(elem, 'utf-8')
    reparsed = minidom.parseString(rough_string)
    return reparsed.toprettyxml(indent="  ").replace('<?xml version="1.0" ?>\n', '')


def get_CPU_RAM_FLOPS(hosts):
    """Return the number of CPU and amount RAM for a host list """
    hosts_attr = {'TOTAL': {'CPU': 0 ,'RAM': 0}}
    cluster_attr = {}
    for host in hosts:
        if isinstance(host, Host):
            host = host.address
        cluster = get_host_cluster(host)
        if not cluster_attr.has_key(cluster):
            attr = get_host_attributes(host)
            cluster_attr[cluster] = {'CPU': attr['architecture']['smt_size'],
                                     'RAM': int(attr['main_memory']['ram_size']/10**6),
                                     'flops': attr['performance']['node_flops'] }
        hosts_attr[host] = cluster_attr[cluster]
        hosts_attr['TOTAL']['CPU'] += attr['architecture']['smt_size']
        hosts_attr['TOTAL']['RAM'] += int(attr['main_memory']['ram_size']/10**6)

    logger.debug(hosts_list(hosts_attr))
    return hosts_attr

def get_fastest_host(hosts):
        """ Use the G5K api to have the fastest node"""
        attr = get_CPU_RAM_FLOPS(hosts)
        max_flops = 0
        for host in hosts:
            if isinstance(host, Host):
                host = host.address 
            flops = attr[host]['flops']
            if  flops > max_flops:
                max_flops = flops
                fastest_host = host
        return fastest_host

def get_max_vms(hosts, mem = 512):
    """Return the maximum number of virtual machines that can be created on the hosts"""
    total = get_CPU_RAM_FLOPS(hosts)['TOTAL']
    return int(total['RAM']/mem-1)


def get_vms_slot(vms, elements, slots, excluded_elements = None):
    """Return a slot with enough RAM and CPU """
    chosen_slot = None

    req_ram = sum( [ vm['mem'] for vm in vms] )
    req_cpu = sum( [ vm['n_cpu'] for vm in vms] ) /3
    logger.debug('RAM %s CPU %s', req_ram, req_cpu)

    for slot in slots:
        hosts = []
        for element in elements:
            n_hosts = slot[2][element]
            if element in get_g5k_clusters():
                for i in range(n_hosts):
                    hosts.append(Host(str(element+'-1.'+get_cluster_site(element)+'.grid5000.fr')))
        attr = get_CPU_RAM_FLOPS(hosts)['TOTAL']
        if attr['CPU'] > req_cpu and attr['RAM'] > req_ram:
            chosen_slot = slot
            break

        del hosts[:]

    if chosen_slot is None:
        return None, None

    resources = {}
    for host in hosts:
        if isinstance(host, Host):
            host = host.address
        if req_ram < 0 and req_cpu < 0:
            break
        attr = get_CPU_RAM_FLOPS([host])
        req_ram -= attr[host]['RAM']
        req_cpu -= attr[host]['CPU']
        cluster = get_host_cluster(host)
        if not resources.has_key(cluster):
            resources[element] = 1
        else:
            resources[element] += 1

    return chosen_slot[0], distribute_hosts(chosen_slot[2], resources, excluded_elements)