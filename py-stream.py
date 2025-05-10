#!/usr/bin/env python3

import argparse, re, sys, os, tempfile, shutil

DEFAULT = 0
IS_LAST = False

# Commands
QUIT = "q"
PRINT = "p"
DELETE = "d"
SUBSTITUTE = "s"
REPLACE_ALL = "g"
STOP_PRINT = "n"
LABEL = ":"
BRANCH = "b"
COND_BRANCH = "t"
APPEND = "a"
INSERT = "i"
CHANGE = "c"


def parse_input(flag_input):
    """ Parses the commandline flag input into a list of flags """
    flag_input = "\n".join(strip_comments(line) for line in flag_input.splitlines())
    raw_flags = re.split("\n", flag_input)
    flags = [flag.strip() for flag in raw_flags if flag.strip()]
    flag_list = []
    address_regex = r"|(?:\$|\d+|(?:\/[^\/]+\/))(?:\s*,\s*(?:\$|\d+|(?:\/[^\/]+\/)))?"
    i = 0
    # For each flag:
    # 1. Conduct a lenient match for manual splitting on ;
    # 2. Split and append the rest to flags
    # 3. Conduct a fullmatch to ensure that we didnt capture anything else
    while i < len(flags):
        flag = flags[i]
        if m := re.search(rf"^({address_regex})?\s*([qpd]);?", flag):
            split_flag(m.group(), flags, i)
            
            if not re.fullmatch(rf"^({address_regex})?\s*([qpd])", flags[i]):
                print("py-stream: command line: invalid command", file=sys.stderr)
                sys.exit(1)

            flag_list.append(
                { "address": parse_addresses(m.group(1)), "flag": m.group(2), "active_range": False }
            )
        elif m := re.search(
            rf"^({address_regex})?{SUBSTITUTE}([^\s])((?:\\.|(?!\2).)*?)\2((?:\\.|(?!\2).)*?)\2({REPLACE_ALL})?;?", flag
        ):  
            split_flag(m.group(), flags, i)

            if not re.fullmatch(rf"^({address_regex})?{SUBSTITUTE}([^\s])((?:\\.|(?!\2).)*?)\2((?:\\.|(?!\2).)*?)\2({REPLACE_ALL})?", flags[i]):
                print("py-stream: command line: invalid command", file=sys.stderr)
                sys.exit(1)

            flag_list.append(
                {
                    "address": parse_addresses(m.group(1)),
                    "flag": SUBSTITUTE,
                    "match": m.group(3).replace("\\", ""),
                    "replace": m.group(4).replace("\\", ""),
                    "modifier": m.group(5),
                    "active_range": False,
                }
            )
        elif m := re.search(rf"^({address_regex})?\s*([aic])\s*(\w+);?", flag):
            split_flag(m.group(), flags, i)

            if not re.fullmatch(rf"^({address_regex})?\s*([aic])\s*(\w+)", flags[i]):
                print("py-stream: command line: invalid command", file=sys.stderr)
                sys.exit(1)

            flag_list.append(
                { "address": parse_addresses(m.group(1)), "flag": m.group(2), "modifier": m.group(3), "active_range": False }
            )
        elif m := re.search(rf":\s*(\w+);?", flag):
            split_flag(m.group(), flags, i)

            if not re.fullmatch(rf":\s*(\w+)", flags[i]):
                print("py-stream: command line: invalid command", file=sys.stderr)
                sys.exit(1)

            flag_list.append(
                { "flag": ':', "label": m.group(1) }
            )
        elif m := re.search(rf"^({address_regex})?\s*([tb])\s*(.+);?", flag):
            if not re.fullmatch(rf"^({address_regex})?\s*([tb])\s*(.+)", flags[i]):
                print("py-stream: command line: invalid command", file=sys.stderr)
                sys.exit(1)
            flag_list.append(
                { "address": parse_addresses(m.group(1)), "flag": m.group(2), "label": m.group(3), "active_range": False }
            )
        else:
            print("py-stream: command line: invalid command", file=sys.stderr)
            sys.exit(1)
        i += 1

    validate_branches(flag_list)
    return flag_list
def strip_comments(line):
    """Manually strip comments since # can also appear in regexes"""
    i = 0
    n = len(line)
    in_regex = False
    escaped = False
    in_substitute = False
    delimiter = ''
    delimiter_count = 0
    new_line = ""

    while i < n:
        char = line[i]

        if escaped:
            new_line += char
            escaped = False
        elif char == '\\':
            new_line += char
            escaped = True
        elif in_substitute:
            new_line += char
            if char == delimiter:
                delimiter_count += 1
                if delimiter_count == 3:
                    in_substitute = False
        elif in_regex:
            new_line += char
            if char == '/':
                in_regex = False
        elif char == '/':
            new_line += char
            in_regex = True
        elif char == 's' and i + 1 < n and not line[i+1].isspace():
            # found an s-command
            delimiter = line[i+1]
            in_substitute = True
            delimiter_count = 0
            new_line += char
        elif char == '#' and not in_regex and not in_substitute:
            break  # comment begins
        else:
            new_line += char

        i += 1

    return new_line


def validate_branches(flags):
    """Checks if for any branch command, they are assigned to a valid label"""
    labels = set()
    for flag in flags:
        if flag.get("flag") == ":":
            labels.add(flag.get("label"))

    # Now, check all branches
    for flag in flags:
        if flag.get("flag") in {"b", "t"}:
            label = flag.get("label")
            if label and label not in labels:
                print(f"py-stream: error", file=sys.stderr)
                sys.exit(1)

def parse_addresses(addresses):
    """Parses the addresses to be interpreted by the main flag handling loop"""
    if not addresses:
        return {
            "start": { "type": "line", "value": DEFAULT },
            "end": None
        }
    
    addresses = addresses.split(",", 1)
    start = parse_address(addresses[0])
    end = parse_address(addresses[1] if len(addresses) > 1 else None)

    return { "start": start, "end": end }


def parse_address(address):
    """Parses the address of commands into start and end conditions"""
    if not address:
        return None
    address = address.strip()
    if re.fullmatch(r"/.*/", address):
        return { "type": "regex", "value": address.strip("/") }
    elif address.isdigit():
        return { "type": "line", "value": int(address) }
    elif address == "$":
        return { "type": "special", "value": DEFAULT }

def split_flag(flag, flags, i):
    """Splits flags if they end with a ; and return the new modified flags"""
    if flag[-1] != ';':
        return
    delim_index = flag.rfind(';')  
    next_flags = flags[i][delim_index + 1:].strip()  
    if next_flags:
        flags.insert(i + 1, next_flags)
    
    flags[i] = flag[:delim_index].strip() 
    return 

def handle_quit(quit_input, line, ln):
    """Checks if the quit condition has been met -> returns True if true"""
    if quit_input is None:
        return
    start = quit_input.get("address").get("start")
    stype = start.get("type")
    value = start.get("value")
    return (stype == "regex" and re.search(value, line)) or (stype == "line" and value == ln)


def handle_substitute(flag, line, ln):
    """Returns the new? line rather than printing, to buffer the substituion in case of conflict with other commands"""
    address = flag.get("address")
    match = flag.get("match")
    replace = flag.get("replace")
    modifiers = flag.get("modifier")

    replace_count = DEFAULT if modifiers and REPLACE_ALL in modifiers else 1
    
    start = address.get("start")
    end = address.get("end")

    sub_line = line

    if not end or (end.get("type") == "line" and end.get("value") < ln):
        if handle_address(start, line, ln):
            sub_line = re.sub(match, replace, line, count=replace_count)
        else:
            sub_line = line
    if handle_address(start, line, ln):
        flag["active_range"] = True

    in_range = flag.get("active_range", False)
        
    if in_range:
        sub_line = re.sub(match, replace, line, count=replace_count)

    if handle_address(end, line, ln):
        flag["active_range"] = False
        
    return sub_line


def handle_address(address, line, ln):
    """Boolean function - checks if the address condition is met"""
    if not address:
        return True
    global IS_LAST
    ftype = address.get("type")
    value = address.get("value")
    
    if ftype == "regex":
        # Ensure that we return a boolean, not the match object itself
        return bool(re.search(value, line))
    
    elif ftype == "line":
        return int(value) == ln or int(value) == DEFAULT
    
    elif ftype == "special":
        return int(value) == DEFAULT and IS_LAST
    
    return False  # Default case if none of the conditions match
def clean(out_stream, path, dest_file):
    """ clean up the temporary file """
    shutil.copyfile(out_stream, dest_file)
    out_stream.close()
    os.remove(path)

def handle_ranges(flag, line, ln):
    """general use case for functions whose range calculations are the same"""
    address = flag.get("address")
    start = address.get("start") if address else None
    end = address.get("end") if address else None

    result = False
    # Checks if the address is a ranged address - if so operate normally
    if not end or (end.get("type") == "line" and end.get("value") < ln):
        result = handle_address(start, line, ln)
    else:
        # If start condition is met then initiate the range
        if handle_address(start, line, ln):
            flag["active_range"] = True

        in_range = flag.get("active_range", False)

        if in_range:
            result =  True

        # If end condition is met then cancel the range
        if handle_address(end, line, ln):
            flag["active_range"] = False

    return result

def handle_delete(flag, line, ln):
    """Special range calculation for delete flag since we need to know if it just entered the range"""
    address = flag.get("address")
    start = address.get("start") if address else None
    end = address.get("end") if address else None

    result = False

    # Same base case
    if not end or (end.get("type") == "line" and end.get("value") < ln):
        result = handle_address(start, line, ln)

    else: 
        # Same range calulation
        just_entered = False
        if handle_address(start, line, ln):
            flag["active_range"] = True
            just_entered = True
        in_range = flag.get("active_range", False)
        if in_range:
            result = True
        # we need to delete the line before we check any end condition once we first enter the range
        if (not just_entered and handle_address(end, line, ln)):
            flag["active_range"] = False

    return result

def handle_change(flag, line, ln):
    """Special handling for the 'change' (c) command."""
    address = flag.get("address")
    start = address.get("start") if address else None
    end = address.get("end") if address else None

    # If no end address or end is before current line, just do normal match
    if not end or (end.get("type") == "line" and end.get("value") < ln):
        return handle_address(start, line, ln), False

    # Range mode
    just_entered = False
    if handle_address(start, line, ln):
        flag["active_range"] = True
        just_entered = True

    in_range = flag.get("active_range", False)

    # If just_entered, suppress output
    if in_range and just_entered:
        return False, False

    # If we reach end of range, output and stop
    if handle_address(end, line, ln):
        flag["active_range"] = False
        return True, True  # trigger output on exiting

    return False, False  # otherwise do nothing


def handle_flags(args, flags, line, ln, out_stream, path, branch_to=""):
    """ Command block for flag processing"""
    if not line: 
        return
    do_append = False  # Keeps track of whether a successful append has been attempted
    append_txt = ""
    default_print = not args.n
    buffer = []  # Initialize a buffer to accumulate lines for later printing
    substitution_done = False  # To track if a substitution was made
    for flag in flags:
        f = flag.get("flag")
        if branch_to and f == ":":
            if flag.get("label") != branch_to:
                continue

        if f == QUIT:
            handle_quit(flag, line, ln) and handle_exit(line, args, out_stream, path)
            continue
        if f == DELETE:
            if handle_delete(flag, line, ln):
                return
        elif f == PRINT:
            if handle_ranges(flag, line, ln):
                buffer.append(line)
        elif f == SUBSTITUTE:
            sub_line = handle_substitute(flag, line, ln)
            if sub_line != line:
                line = sub_line
                substitution_done = True
        elif f == INSERT:
            modifier = f"{flag.get('modifier')}\n"
            if handle_ranges(flag, line, ln):
                buffer.append(modifier)
        elif f == APPEND:
            modifier = f"{flag.get('modifier')}\n"
            if handle_ranges(flag, line, ln):
                do_append = True
                append_txt = modifier
        elif f == CHANGE:
            modifier = f"{flag.get('modifier')}\n"
            result, just_exited = handle_change(flag, line, ln)
            if just_exited:
                buffer.append(modifier)
                break
            if result:
                default_print = False


        elif f == BRANCH:  # Branch to a label unconditionally
            label_name = flag.get("label")
            handle_flags(args, flags, line, ln, out_stream, path, label_name)
            return

        elif f == COND_BRANCH:  # Test if a substitution was made, then branch
            label_name = flag.get("label")

            if substitution_done:
                handle_flags(args, flags, line, ln, out_stream, path, label_name)
                return
        # Exclude label flag since its not actually a command we use to process data directly
        elif f != ':':
            print("py-stream: command line: invalid command", file=sys.stderr)
            sys.exit(1)

    # checks if n flag is toggled on
    if default_print:
        buffer.append(line)
        # required two variables for appending since just adding to the buffer would behave more like insert
        do_append and buffer.append(append_txt)

    # Empty out the buffer
    for buffered_line in buffer:
        print(buffered_line, end="", file=out_stream)


def handle_exit(line, args, outstream, path):
    """ handle exit for the script """
    print(line, end="", file=outstream)
    if args.i:
        clean(outstream, path, outstream) 
    sys.exit(0)

def main():
    """ main function to parse command line arguments and execute the script """
    global IS_LAST

    # Argparse commands setup
    parser = argparse.ArgumentParser(
        usage="py-stream [-i] [-n] [-f <script-file> | <sed-command>] [<files>...]"
    )

    parser.add_argument("-i", action="store_true", help="Stops printing to stdout by default, instead prints to given file")
    parser.add_argument("-n", action="store_true", help="Stops printing by default")
    parser.add_argument("-f", "--script-file", nargs="?", help="Script file to be executed")
    parser.add_argument("flags", nargs="?", default=None, help="flags for py-stream")
    parser.add_argument("files", nargs="*", default=[], help="Input files")
    args = parser.parse_args()

    # Checks if commands are given
    if not (args.flags or args.script_file):
        parser.print_usage(file=sys.stderr)
        sys.exit(1)

    # Handles weird case were if script_file is given, the files are read in as flags
    if args.flags and args.script_file:
        args.files.insert(0, args.flags)
        args.flags = None

    # now parse the flags
    if args.script_file:
        with open(args.script_file) as f:
            flags = parse_input(f.read())
    else:
        flags = parse_input(args.flags)

    # No output stream detected
    if args.i and not args.files:
        parser.print_usage(file=sys.stderr)
        sys.exit(1)

    ln = 1
    prev = None
    out_stream = sys.stdout
    temp_path = None
    temp_file = None

    # Separate stream logic for i flag toggle
    # Streams are considered independant, line number starts at 1 for all stream
    # Last line, is the last line of the stream as opposed to the last line being processed
    if args.i:
        for file in args.files:
            stream = open(file, "r") if file else sys.stdin

            ln = 1
            prev = None
            fd, temp_path = tempfile.mkstemp()
            temp_file = os.fdopen(fd, "w+")
            out_stream = temp_file

            try:
                for line in stream:
                    if prev is not None:
                        handle_flags(args, flags, prev, ln, out_stream, temp_path)
                        ln += 1
                    prev = line

                if prev and args.i:
                    IS_LAST = True
                    handle_flags(args, flags, prev, ln, out_stream, temp_path)
                    IS_LAST = False

            except KeyboardInterrupt:
                sys.exit(0)
            finally:
                if stream != sys.stdin:
                    stream.close()
                
            if args.i and file:
                temp_file.flush()
                temp_file.seek(0)
                with open(file, "w") as f:
                    shutil.copyfileobj(temp_file, f)
                temp_file.close()
                os.remove(temp_path)
    # Here the streams are 'collected' and line number is never reset 
    # Last line referers to the last line in the collected streams
    else:
        # Opens all the streams for reading
        streams = [open(f, "r") for f in args.files] if args.files else [sys.stdin]

        try:
            for stream in streams:
                for line in stream:
                    if prev is not None:
                        handle_flags(args, flags, prev, ln, out_stream, None)
                        ln += 1
                    prev = line
        finally:
            for stream in streams:
                if stream != sys.stdin:
                    stream.close()

        if prev is not None:
            IS_LAST = True
            handle_flags(args, flags, prev, ln, out_stream, None)

if __name__ == "__main__":
    main()