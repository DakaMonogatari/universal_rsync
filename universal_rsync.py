#!/usr/bin/python3

import re, os, sys, subprocess, shlex
import xml.etree.ElementTree as ET
import argparse, unicodedata

SITES_DEFAULT="~/.config/universal_rsync/transfer_sites.xml"
SITE_TYPES=["local", "external_drive", "remote_server", "android_device", "snapshot", "custom"]
QUIET_LVL=0
WARNING_COLOR="\033[1;33m"
ERROR_COLOR = "\033[1;31m"
RESET_COLOR="\033[0m"


def query_yes_no(question, default="yes"):

    valid = {"yes": True, "y": True, "ye": True, "no": False, "n": False}
    if default is None:
        prompt = " [y/n] "
    elif default == "yes":
        prompt = " [Y/n] "
    elif default == "no":
        prompt = " [y/N] "
    else:
        raise ValueError("invalid default answer: '%s'" % default)

    while True:
        sys.stdout.write(question + prompt)
        choice = input().lower()
        if default is not None and choice == "":
            return valid[default]
        elif choice in valid:
            return valid[choice]
        else:
            sys.stdout.write("Please respond with 'yes' or 'no' " "(or 'y' or 'n').\n")


def check_children(node, expected_children):

    count = 0
    expected_count = len(node.findall("./*"))
    for child in expected_children:
        count = count + len(node.findall(f"./{child}"))
    
    return ( count == expected_count )


def validate_sites(sites):

    global QUIET_LVL

    errors = 0

    # GO BACK TO THIS ONE
    if sites is None:
        print(f"{ERROR_COLOR}ERROR - Sites file is empty.{RESET_COLOR}\n")
        errors = errors + 1

    if len(sites.findall("./notification[@type='success']")) > 1 or len(sites.findall("./notification[@type='failure']")) > 1 or len(sites.findall("./notification")) > 2:
        print(f"{ERROR_COLOR}ERROR - Too many notification scripts in sites file.{RESET_COLOR}\n")
        errors = errors + 1

    if len(sites.findall("./site")) < 1:
        print(f"{ERROR_COLOR}ERROR - No sites configured in sites file.{RESET_COLOR}\n")
        errors = errors + 1

    if not check_children(sites, ['notification', 'site']):
        print(f"{ERROR_COLOR}ERROR - Unknown elements found [NODE: sites].{RESET_COLOR}\n")
        errors = errors + 1

    ids = []
    for site in sites.findall('site'):

        if not site.get('id') or not site.get('name'):
            print(f"{ERROR_COLOR}ERROR - Site {site} has no name or ID.{RESET_COLOR}\n")
            errors = errors + 1

        if site.get('id') in ids:
            print(f"{ERROR_COLOR}ERROR - One or more sites with identical IDs exist.{RESET_COLOR}\n")
            errors = errors + 1
        else:
            ids.append(site.get('id'))

        if not check_children(site, ['source', 'destination', 'params', 'flags', 'filters']):
            print(f"{ERROR_COLOR}ERROR - Unknown elements found [NODE: site {site.get('id')}].{RESET_COLOR}\n")
            errors = errors + 1

        if len(site.findall("./source")) != 1 or len(site.findall("./destination")) != 1 :
            print(f"{ERROR_COLOR}ERROR - Site {site.get('id')} contains bad number of sources/destinations (must be exactly 1 of each){RESET_COLOR}.\n")
            errors = errors + 1
        elif not site.find("./source").text or not site.find("./destination").text:
            print(f"{ERROR_COLOR}ERROR - Site {site.get('id')} contains incomplete source/destination.{RESET_COLOR}\n")
            errors = errors + 1
        elif site.find("./source").get('type') is None or site.find("./destination").get('type') is None:
            print(f"{ERROR_COLOR}ERROR - Site {site.get('id')}'s source/destination does not have a type (see: [-t] for list of available types).{RESET_COLOR}\n")
            errors = errors + 1
        elif site.find("./source").get('type') not in SITE_TYPES or site.find("./destination").get('type') not in SITE_TYPES:
            print(f"{ERROR_COLOR}ERROR - Site {site.get('id')} contains unknown type of source/destination (see: [-t] for list of available types).{RESET_COLOR}\n")
            errors = errors + 1

    for entry in list(sites.iter()):
        if entry.text is None:
            if not QUIET_LVL > 0: print(f"{WARNING_COLOR}WARNING - Empty entries exist in site file. May cause problems with program execution.{RESET_COLOR}\n")
            break
            
    return errors


def get_sites_root(sites_location):
    tree = ET.parse(sites_location)
    sites = tree.getroot()
    return sites


def get_site_params(site):

    site_params = {}
    for param in site.find('params').findall('param'):
        site_params[param.get('type')] = param.text

    return site_params


def get_site_flags(site):

    global QUIET_LVL

    arg = ""
    site_flags = []

    if len(list(site.iter('flags')) + list(site.iter('flag'))) == 0:
        if not QUIET_LVL > 0: print(f"{WARNING_COLOR}WARNING - No flags found for site {site.get('id')}. Skipping...{RESET_COLOR}\n")
        return site_flags

    for flag in site.find('flags').findall('flag'):
        arg = ""
        if not flag.text: continue
        if flag.get('is_long') == "true":
            arg = "--" + flag.text 
        else:
            arg = "-" + flag.text 
        site_flags.append(arg)
        arg = ""

    return site_flags


def get_site_filters(site):

    global QUIET_LVL

    arg = ""
    site_filters = []

    if len(list(site.iter('filters')) + list(site.iter('filter'))) == 0:
        if not QUIET_LVL > 0: print(f"{WARNING_COLOR}WARNING - No filters found for site {site.get('id')}. Skipping...{RESET_COLOR}\n")
        return site_filters

    for site_filter in site.find('filters').findall('filter'):
        flag = ""
        if (site_filter.get('type') == "include" or site_filter.get('type') == "exclude") and site_filter.text:
            flag = "--" + site_filter.get('type')
            site_filters.append(flag)
            site_filters.append(site_filter.text)

    return site_filters


def site_exists(sites, site_id):
    for site in sites.findall('site'):
        if site.get('id') == site_id:
            return True
    return False


def site_is_available(sites, site_id):

    global QUIET_LVL

    available = True
    exists = False

    for site in sites.findall('site'):
        if site.get('id') == site_id:
            exists = True
            for location in [site.find('source'), site.find('destination')]:
                match location.get('type'):

                    case "local":
                        available = available and (True if os.path.exists(location.text) and os.path.isdir(location.text) else False)

                    case "external_drive":
                        for i in range(len(location.text) + 1):
                            if os.path.ismount(location.text[:i]):
                                available = True
                                break
                            available = False
                        available = available and (True if os.path.exists(location.text) and os.path.isdir(location.text) else False)

                    case "remote_server":

                        location_user = re.search(r"^.*?\@", location.text)
                        location_user = location_user.group(0)[:-1] if location_user != None else ""

                        location_domain = re.search(r"^.*\:", location.text)
                        if location_domain != None:
                            location_domain = re.sub(r"^.*\@", "" , location_domain.group(0)[:-1])
                        else:
                            if not QUIET_LVL > 1: print(f"ERROR: IP Address or Domain Name not detected for site {site.get('name')}.\n")
                            return False

                        location_directory = re.search(r"\:(\/|\~\/)?([a-zA-Z0-9_\- ]+\/?)+|\:\/|\:\~\/?" , location.text)
                        if location_directory != None:
                            location_directory = location_directory.group(0)[1:]
                        else:
                            if not QUIET_LVL > 1: print(f"ERROR: Remote location not detected for site {site.get('name')}.\n")
                            return False

                        # location_directory_full = (location_user + "@" if location_user != "" else "") + location_domain + ":" + location_directory
                        location_domain_full = (location_user + "@" if location_user != "" else "") + location_domain

                        params = get_site_params(site)                        

                        ssh_port = params['ssh_port']
                        ssh_key_location = params['ssh_key_location']

                        writeable = 1
                        if subprocess.run(["ping", "-c", "1", "-w", "5", f"{location_domain}"], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT).returncode == 0:
                            writeable = subprocess.run(["ssh", "-p", f"{ssh_port}", "-i", f"{ssh_key_location}" , f"{location_domain_full}",  f"[[ -d {location_directory} ]]"], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
                        available = available and (True if (writeable != 1 and writeable.returncode == 0) else False)

                    case "android_device":

                        # everything here except os.path.exists() horrible and redundant, but I had to do it

                        uid = os.getuid()
                        mtp_location = re.sub(f"\/run\/user\/{uid}\/gvfs\/mtp\:host\=", "mtp://", location.text)

                        try:
                            gio1 = subprocess.check_output(["gio", "info", f"{location.text}"], stderr=subprocess.STDOUT)
                        except subprocess.CalledProcessError as e:
                            gio1 = str(e.output)
                        try:
                            gio2 = subprocess.check_output(["gio", "info", f"{mtp_location}"], stderr=subprocess.STDOUT)
                        except subprocess.CalledProcessError as e:
                            gio2 = str(e.output)

                        available = available and (True if os.path.exists(location.text) and gio1 == gio2 else False)

                    case "snapshot":
                        available = False

                    case _:
                        if not QUIET_LVL > 1: print("ERROR: Location type doesn't exist\n")
                        return False
            break

    return ( available and exists )


def get_sites(sites, source_type=[], destination_type=[], all_flag=False):
    
    site_infos = []

    for site in sites.findall('site'):
        site_info = [site.get('id'), site.get('name'), site.find('source').get('type'), site.find('destination').get('type'), site.find('source').text, site.find('destination').text]
        if (source_type is None or site.find('source').get('type') in source_type) and (destination_type is None or site.find('destination').get('type') in destination_type):
            if all_flag:
                site_infos.append(site_info)
            elif site_is_available(sites, site.get('id')):
                site_infos.append(site_info)

    return site_infos


def compile_rsync_command(sites, site_id, DRY_RUN=False):

    global QUIET_LVL

    command_subproc = [["rsync"]]
    command_postproc = [[]]

    try:
        subprocess.run(["rsync", "-h"], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    except OSError as e:
        print("ERROR - Rsync not found.\n", e, "\n")
        sys.exit(0)


    if not site_is_available(sites, site_id):
        if not QUIET_LVL > 1: print(f"{ERROR_COLOR}ERROR - Site {site_id} not available.{RESET_COLOR}\n")
        return 1

    # FIND SITE
    for site in sites.findall('site'):
        if site.get('id') == site_id:

            site_params = get_site_params(site)
            site_source = site.find('source').text
            site_destination = site.find('destination').text

            # CONSTRUCT SSH-SPECIFIC FLAG
            if site.find('source').get('type') == "remote_server" or site.find('destination').get('type') == "remote_server":
                if site_params["ssh_port"] != None and site_params["ssh_key_location"] != None:
                    e = "ssh -p " + site_params["ssh_port"] + " -i " + site_params["ssh_key_location"]
                    command_subproc[0].extend(["-e", e ])
                else:
                    if not QUIET_LVL > 1: print(f"{ERROR_COLOR}ERROR - Unable to compile SSH command for site {site_id} - Missing arguments.{RESET_COLOR}\n")
                    return 1

            # CONSTRUCT SNAPSHOT-SPECIFIC FLAG AND POST-PROCESSING
            if site.find('destination').get('snapshot') == "true":
                if "/" not in site_params['snap_base']:

                    snap_base = site_params['snap_base'] if site_params['snap_base'] else "default."
                    snap_extension = ""

                    if site_params['snap_extension'].lower() == "date":
                        snap_extension = subprocess.check_output(['date', '+%Y-%b-%d:_%T'], encoding='UTF-8')
                    else:
                        temp_proc = subprocess.Popen(['find', site_destination, '-mindepth', '1', '-maxdepth', '1', '!', '-type', 'l', '-iname', f"{site_params['snap_base']}*"], stdout=subprocess.PIPE)
                        snap_extension = subprocess.check_output(['wc', '-l'], stdin=temp_proc.stdout, encoding='UTF-8')
                        temp_proc.wait()

                    snap_name = snap_base + snap_extension
                    snap_name = snap_name[:-1]
                    site_destination_last = site_destination + ( "/last" if site_destination[-1] != "/" else "last" )          
                    command_subproc[0].extend(["--link-dest", site_destination_last ])
                    site_destination = site_destination + ( f"/{snap_name}" if site_destination[-1] != "/" else f"{snap_name}" )

                    command_postproc = [['rm', '-f', site_destination_last], ['ln', '-s', site_destination, site_destination_last]]

                else:
                    if not QUIET_LVL > 1: print(f"{ERROR_COLOR}ERROR - Unable to compile snapshot command for site {site_id} - Trailing slash in base name.{RESET_COLOR}\n")
                    return 1
                
                
            # HANDLE FLAGS
            command_subproc[0].extend(get_site_flags(site))
            if DRY_RUN == True: command_subproc[0].append("--dry-run")

            # HANDLE FILTERS
            command_subproc[0].extend(get_site_filters(site))

            # HANDLE SOURCE DIRECTORY
            arg=""
            if site.find('source').get('preserve_dir') == "true":
                arg = (site_source if site_source[-1] != "/" else site_source[:-1])
            else:
                arg = (site_source if site_source[-1] == "/" else site_source + "/")
            command_subproc[0].append(arg)

            # HANDLE DESTINATION DIRECTORY
            command_subproc[0].append(site_destination)

            # HANDLE POST-PROCESSING
            if DRY_RUN == False and command_postproc != [[]]: command_subproc.extend(command_postproc)

            break

    if command_subproc != ["rsync"]:
        return command_subproc
    else:
        return 1

def run_notification_script(sites, site_id_list, return_code):

    global QUIET_LVL

    script_type = "failure" if return_code > 0 else "success"
    script_command = ""

    for script in sites.findall('notification'):
        if script.get('type') == script_type:
            script_command = re.sub("\%ID", f"{site_id_list}" , script.text)
    
    if script_command:
        subprocess.run(shlex.split(script_command))
    else:
        if not QUIET_LVL > 0: print(f"{WARNING_COLOR}WARNING - No valid notification script found. Skipping...{RESET_COLOR}\n")
    

def print_site_list(site_list, list_all):

    univ_l= 5
    ls = [0] * 6
    kanjicount = []
    title = ["SITE ID" , "SITE NAME", "TRANSFER TYPE", "TRANSFER SOURCE", "TRANSFER DESTINATION"]

    for site in site_list:
        for i in range(len(ls)):
            ls[i] = ls[i] if ls[i] > len(site[i]) else len(site[i])
        kanjicount.append([0] * 6)

    for site in site_list:
        for i in range(len(ls)):
            for k in site[i]:
                if unicodedata.east_asian_width(k) == "W":
                    kanjicount[site_list.index(site)][i] = kanjicount[site_list.index(site)][i] - 1


    ls_title = [0] * 6
    bar = ""
    formatstr= ""
    for i in range(len(ls_title)):
        ls_title[i] = ls[i] + univ_l
    ls_title = [ls_title[0], ls_title[1], ls_title[2]+ls_title[3], ls_title[4], ls_title[5]]
    for i in range(len(ls_title)):
        formatstr = formatstr + "{:<" + str(ls_title[i]) + "}"
        bar = bar + ("-" * (ls_title[i] - univ_l)) + (" " * univ_l)       

    print("LISTING ALL", "CONFIGURED" if list_all else "AVAILABLE", "TRANSFER SITES\n")
    print(formatstr.format(*title))
    print(bar)
    # BAR SLIGHTLY OFFSET, FIGURE OUT WHY

    for site in site_list:
        formatstr=""
        ls_site = [0] * 6
        for i in range(len(ls_site)):
            ls_site[i] = ls[i] + kanjicount[site_list.index(site)][i] + univ_l
        ls_site = [ls_site[0], ls_site[1], ls_site[2]+ls_site[3], ls_site[4], ls_site[5]]
        for i in range(len(ls_site)):
            ls_site[i] = ls_site[i] if ls_site[i] > len(title[i]) else ( len(title[i]) + univ_l )
        for i in range(len(ls_site)):
            formatstr = formatstr + "{:<" + str(ls_site[i]) + "}"
        print(formatstr.format(site[0], site[1], f"{site[2]}->{site[3]}", site[4], site[5]))

    print()

def main():

    # IT'S PARSIN' TIME
    parser = argparse.ArgumentParser(description="Perform rsync operations using a pre-configured list of transfer sites")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("-l", "--list", help="list all available transfer sites", action="store_true")
    group.add_argument("-L", "--list-all", help="list all configured transfer sites (including unavailable ones)", action="store_true")
    group.add_argument("-t", "--list-types", help="list all known transfer site types (e.g. 'local', 'remote_server'...)", action="store_true")
    parser.add_argument("--source", help="filter list of transfer sites by source type (used with [-l|-L], view available types with [-t])", action="extend", nargs="+", type=str)
    parser.add_argument("--destination", help="filter list of transfer sites by destination type (used with [-l|-L], view available types with [-t])", action="extend", nargs="+", type=str)
    group.add_argument("-s", "--sites", help="select transfer sites to process", action="extend", nargs="+", type=str)
    parser.add_argument("-n", "--dry-run", help="turn all queued site transfers into dry runs (see: rsync(1) [-n|--dry-run])", action="store_true")
    parser.add_argument("-q", "--quiet-level", help="set quietness level of site transfers ([] - print errors and warnings, [-q] - print errors only, [-qq...] - print critical errors only, default: [])", action="count", default=0)
    parser.add_argument("-p", "--prompt-frequency", help="set prompt frequency of site transfers ([] - don't prompt, [-p] - prompt only once, [-pp...] - prompt once for each rsync command, default: [])", action="count", default=0)
    parser.add_argument("--notify-each", help="run configured notification script once after each site transfer (default:　runs once after all site transfers are done)", action="store_true")
    parser.add_argument("-i", "--input-file", help=f"set custom file location to process transfer sites from (default: {SITES_DEFAULT})", action="store", nargs=1, type=str)
    
    print()

    if len(sys.argv) == 1:
        parser.print_usage()
        print()
        sys.exit(1)

    args = parser.parse_args()

    # QUIET LEVEL AND PROMPT FREQUENCY
    global QUIET_LVL
    QUIET_LVL = args.quiet_level
    prompt_frequency = args.prompt_frequency

    # LIST TYPES
    if args.list_types == True:
        response = "Currently implemented types of transfer sites are: "
        for site_type in SITE_TYPES:
            response = response + site_type + ", "
        print(response[:-2], "\n")
        sys.exit(0)

    # FIND INPUT FILE
    if args.input_file != None and os.path.isfile(os.path.abspath(os.path.expanduser(args.input_file))):
        if not QUIET_LVL > 1: print ("Using custom site location:", os.path.abspath(os.path.expanduser(args.input_file)), "\n")
        sites = get_sites_root(os.path.abspath(os.path.expanduser(args.input_file)))
    elif os.path.isfile(os.path.abspath(os.path.expanduser(SITES_DEFAULT))):
        if not QUIET_LVL > 1: print ("Using default site location:", os.path.abspath(os.path.expanduser(SITES_DEFAULT)), "\n")
        sites = get_sites_root(os.path.abspath(os.path.expanduser(SITES_DEFAULT)))
    else:
        print ("ERROR: No site locations found. Exiting...\n")
        sys.exit(1)

    # VALIDATE INPUT FILE
    errors = validate_sites(sites)
    if errors > 0:
        print("Number of errors:", errors, "\nExiting...\n")
        sys.exit(1)

    # CONFIGURE FILTER
    if (args.source != None or args.destination != None) and args.list != True and args.list_all != True:   
        print("ERROR: --source and --destination can only be used on a list (see: [-l|-L])\n")
        sys.exit(1)

    # LIST SITES
    if args.list == True or args.list_all == True:
        site_list = get_sites(sites, args.source, args.destination, args.list_all)
        print_site_list(site_list, args.list_all)
        sys.exit(0)

    # WHERE THE MAGIC HAPPENS
    elif args.sites != None:
        site_id_list = ""
        for site_id in args.sites:
            site_id_list = site_id_list + site_id + ", "
            if not site_exists(sites, site_id):
                print("ERROR: One or more supplied sites do not exist. Exiting...\n")
                sys.exit(1)
        site_id_list = site_id_list[:-2]

        commands_list = []
        all_skipped = True
        return_code = 0
        final_return_code = 0

        for site_id in args.sites:
            commands_list.append(compile_rsync_command(sites, site_id, args.dry_run))

        query_1 = "Do you wish to run the rsync commands for sites " + site_id_list + (" (WARNING: ERROR IN ONE OR MORE SITES DETECTED)?" if 1 in commands_list else "?")
        if prompt_frequency != 1 or (prompt_frequency == 1 and query_yes_no(query_1)):
            for commands in commands_list:
                final_return_code = 0
                curr_site_id = site_id_list.split(", ")[commands_list.index(commands)]
                query_2 = "Do you wish to run the rsync command for site " + curr_site_id + (" (WARNING: ERROR IN SITE DETECTED)?" if commands == 1 else "?")
                if prompt_frequency != 2 or (prompt_frequency == 2 and query_yes_no(query_2)):
                    all_skipped = False
                    for command in commands:
                        return_code = subprocess.run(command).returncode if command != 1 else command
                        final_return_code  = final_return_code + return_code
                        if return_code != 0: break
                    if args.notify_each: run_notification_script(sites, curr_site_id, final_return_code)
                    print()
                else:
                    if not QUIET_LVL > 1: print("Skipping command...\n")
                    else: print()
            if not (args.notify_each or all_skipped): run_notification_script(sites, site_id_list, final_return_code)
        else:
            if not QUIET_LVL > 1: print("Skipping all commands...\n")
            else: print()

    # END OF MAIN

if __name__ == '__main__':
	try:
		main()
	except KeyboardInterrupt:
		print("Keyboard interrupted received. Exiting...\n")
		try:
			sys.exit(0)
		except SystemExit:
			os._exit(0)

# TO-DO
# REVERSE FLAG
# FLAG FOR SHOWING COMMANDS TO RUN DURING PROMPT, ALSO FLAG FOR NOT RUNNING NOTIFICATION SCRIPT
# FINISH IMPLEMENTING snap_count
# FIX FORMATTING, DISPLAY x->y(snapshot) TYPE FOR SNAPSHOTS
# THINK OF OTHER WAYS TO STAMP SNAPS
# ONE DAY, CLEAN UP COMPILE_RSYNC FUNCTION INTO SUBROUTINES AND PROPERLY USE AN ARRAY FROM START TO FINISH