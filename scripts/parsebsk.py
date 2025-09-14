#!/usr/bin/env python

import argparse
import glob
import json
import logging
import re
import sys
import unittest

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("parsebsk")


def parse_bsk_file(filepath: str) -> dict:
    """
    Parse a .bsk BespokeSynth file.

    Args:
        filepath (str): Path to the .bsk file.

    Returns:
        dict: Dictionary with keys 'json' (parsed JSON) and 'module_state' (hex string or None).
    """
    try:
        with open(filepath, 'rb') as f:
            length_bytes = f.read(8)
            if len(length_bytes) < 8:
                raise ValueError("File too short to contain length prefix.")
            length = int.from_bytes(length_bytes, 'little')
            # Heuristic: if length is unreasonably large, try 32-bit mode
            if length > 100_000_000:
                f.seek(0)
                length_bytes = f.read(4)
                length = int.from_bytes(length_bytes, 'little')
            json_bytes = f.read(length)
            json_str = json_bytes.decode('utf-8')
            data = json.loads(json_str)
            # Read the remainder as binary module state
            module_state = f.read()
            return {
                'json': data,
                'module_state': module_state.hex() if module_state else None
            }
    except Exception as e:
        print(f"Error parsing .bsk file: {e}", file=sys.stderr)
        sys.exit(1)


def write_bsk_file(filepath: str, data: dict, use_32bit: bool = False) -> None:
    """
    Write a .bsk BespokeSynth file with a length-prefixed JSON string.
    Only writes the JSON portion, not module binary state.

    Args:
        filepath (str): Path to output .bsk file.
        data (dict): Dictionary with 'json' and optional 'module_state'.
        use_32bit (bool, optional): Use 32-bit length prefix. Defaults to False.

    Returns:
        None
    """
    # Accepts a dict with 'json' and optional 'module_state' (hex string or bytes)
    json_bytes = json.dumps(data['json'], indent=2).encode('utf-8')
    length = len(json_bytes)
    with open(filepath, 'wb') as f:
        if use_32bit:
            f.write(length.to_bytes(4, 'little'))
        else:
            f.write(length.to_bytes(8, 'little'))
        f.write(json_bytes)
        # Write module_state if present
        module_state = data.get('module_state')
        if module_state:
            if isinstance(module_state, str):
                # Assume hex string
                f.write(bytes.fromhex(module_state))
            elif isinstance(module_state, bytes):
                f.write(module_state)


### tests
import tempfile, os
from pathlib import Path

TEST_BSK_PATH = Path(__file__).parent.parent / 'resource' / 'userdata_original' / 'savestate'


class TestBskRoundTrip(unittest.TestCase):
    def test_round_trip(self) -> None:
        """
        Test round-trip parsing and writing of .bsk files.
        """
        bsk_files = glob.glob(str(TEST_BSK_PATH / '*.bsk'))
        #log.debug(('bsk_files', bsk_files))
        for bsk_path in bsk_files:
            with self.subTest(bsk_file=bsk_path):
                #print(('bsk_path', bsk_path))
                original = parse_bsk_file(bsk_path)
                # Write to temp file
                with tempfile.NamedTemporaryFile() as tmp:
                    tmp_path = tmp.name
                    write_bsk_file(tmp_path, original)
                    reread = parse_bsk_file(tmp_path)
                    # Compare json and module_state
                    self.assertEqual(original['json'], reread['json'])
                    self.assertEqual(original['module_state'], reread['module_state'])
                    #print(original['json'])
                    #print(original['module_state'])

### end tests



# --- Module binary parser registry ---
class TestModuleTypeToCppName(unittest.TestCase):
    def test_module_type_to_cpp_name(self):
        cases = {
            'adsrdisplay': 'ADSRDisplay',
            'arrangementcontroller': 'ArrangementController', # fallback
            'audiotocv': 'AudioToCV',
            'eqmodule': 'EQModule',
            'fft': 'FFT',
            'karplusstrongvoice': 'KarplusStrongVoice', # is this even a 
            'lfo': 'LFO',
            'midiclockin': 'MidiClockIn',
            'oscillator': 'Oscillator',
            'vstplugin': 'VSTPlugin',
        }
        for modtype, expected in cases.items():
            with self.subTest(modtype=modtype):
                self.assertEqual(module_type_to_cpp_name(modtype), expected)


def list_all_modules(source_dir: str) -> dict[str, str]:
    """
    List all C++ module source files in the given directory.

    Args:
        source_dir (str): Path to the source directory.

    Returns:
        dict[str, str]: Mapping from lowercased module name to C++ class/source name.
    """
    modules = []
    srcdir = Path(source_dir)
    for filepath in sorted(glob.glob(str(srcdir / '*.cpp'))):
        path = Path(filepath)
        keyval = (path.name.removesuffix('.cpp').lower(), path.name.removesuffix('.cpp'))
        #print(('keyval', keyval))
        modules.append(keyval)

    return dict(modules)


MODULE_REGISTRY = None

def module_type_to_cpp_name(module_type: str, registry: dict = None) -> str:
    """
    Transform a .bsk module type string to C++ class/source name by looking for a matching module name in the registry.

    Args:
        module_type (str): Module type string from .bsk file.
        registry (dict, optional): Mapping from module name to C++ class/source name. Defaults to None.

    Returns:
        str: C++ class/source name.

    Raises:
        ValueError: If no matching module is found.
    """
    if registry is None:
        global MODULE_REGISTRY
        if MODULE_REGISTRY is None:
            MODULE_REGISTRY = list_all_modules(Path(__file__).parent.parent / "Source")
            log.debug(("MODULE_REGISTRY", MODULE_REGISTRY))
        registry = MODULE_REGISTRY
    output = registry.get(module_type)
    if output:
        return output
    else:
        raise ValueError(f"No cpp module matching {module_type=}")


def find_binary_parsing_modules(source_dir: str) -> list:
    """
    Find modules with binary parsing logic (LoadState/SaveState) in the source directory.

    Args:
        source_dir (str): Path to the source directory.

    Returns:
        list: List of filenames with binary parsing logic.
    """
    modules = []
    for fname in os.listdir(source_dir):
        if not fname.endswith('.cpp'):
            continue
        fpath = os.path.join(source_dir, fname)
        try:
            with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                # Look for LoadState or SaveState function definitions
                if re.search(r'void\s+\w+::(LoadState|SaveState)\s*\(', content):
                    modules.append(fname)
        except Exception as e:
            log.warning(f"Error reading {fpath}: {e}")
    return modules


def dummy_parser(data: bytes) -> dict:
    """
    Dummy parser for binary module state.

    Args:
        data (bytes): Raw binary data.

    Returns:
        dict: Dictionary with 'raw_bytes' key.
    """
    return {'raw_bytes': data}


def parse_idrawablemodule_state(data: bytes) -> dict:
    """
    Parse binary module state according to IDrawableModule::LoadState.

    Args:
        data (bytes): Raw binary data.

    Returns:
        dict: Parsed fields and raw bytes for unknown/complex parts.
    """
    """
    Parse binary module state according to IDrawableModule::LoadState.
    Returns a dict with parsed fields and raw bytes for unknown/complex parts.
    """
    import struct
    from io import BytesIO
    f = BytesIO(data)
    result = {}
    try:
        # Read moduleRev (int32), baseRev (int32)
        moduleRev = struct.unpack('<i', f.read(4))[0]
        baseRev = struct.unpack('<i', f.read(4))[0]
        result['moduleRev'] = moduleRev
        result['baseRev'] = baseRev
        # If baseRev > 2, read pinned info
        if baseRev > 2:
            mPinned = struct.unpack('<?', f.read(1))[0]
            mPinnedPosition_x = struct.unpack('<f', f.read(4))[0]
            mPinnedPosition_y = struct.unpack('<f', f.read(4))[0]
            result['mPinned'] = mPinned
            result['mPinnedPosition'] = (mPinnedPosition_x, mPinnedPosition_y)
        # Read numUIControls
        numUIControls = struct.unpack('<i', f.read(4))[0]
        result['numUIControls'] = numUIControls
        controls = []
        for _ in range(numUIControls):
            # Read control name (assume length-prefixed string)
            name_len = struct.unpack('<i', f.read(4))[0]
            uicontrolname = f.read(name_len).decode('utf-8')
            control = {'name': uicontrolname}
            if baseRev >= 2:
                rawValue = struct.unpack('<f', f.read(4))[0]
                control['rawValue'] = rawValue
            # Skipping actual control state parsing for now
            # Skip separator
            sep = f.read(4)
            control['separator'] = sep.hex()
            controls.append(control)
        result['controls'] = controls
        # Skipping children and patch cable sources for now
        # Store remaining bytes
        remaining_bytes = f.read()
        # Try to parse child modules using registry if possible
        # Example: if you know the child module type, use registry[module_type]['parser']
        result['remaining'] = remaining_bytes.hex()
        # If you have module type info, you could do:
        # registry = build_parser_registry(source_dir)
        # module_type = ... # get from context
        # if module_type in registry:
        #     result['child_module'] = registry[module_type]['parser'](remaining_bytes)
    except Exception as e:
        result['error'] = str(e)
        result['raw_bytes'] = data.hex()
    return result


def build_parser_registry(source_dir: str) -> dict:
    """
    Build a registry of modules with binary parsing logic from the source directory.

    Args:
        source_dir (str): Path to the source directory.

    Returns:
        dict: Registry mapping module names to parser info and method sources.
    """
    registry = {}
    method_regex = re.compile(r'(void\s+\w+::(LoadState|SaveState)\s*\([^)]*\)\s*\{[\s\S]*?^\})', re.MULTILINE)
    for mod in find_binary_parsing_modules(source_dir):
        module_name = mod.replace('.cpp', '')
        source_path = os.path.join(source_dir, mod)
        # Extract method sources
        try:
            with open(source_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                methods = {}
                for match in method_regex.finditer(content):
                    method_src = match.group(0)
                    method_name = match.group(2)
                    methods[method_name] = method_src
        except Exception as e:
            log.warning(f"Error extracting methods from {source_path}: {e}")
            methods = {}
        # Detect if IDrawableModule::LoadState is present
        has_idrawable_loadstate = any(
            'IDrawableModule::LoadState' in msrc for msrc in methods.values()
        )
        has_idrawable_savestate = any(
            'IDrawableModule::SaveState' in msrc for msrc in methods.values()
        )
        # Always add dummy parser for fallback
        registry[module_name] = {
            'source': source_path,
            'lowerName': Path(source_path).name.lower(),
            'parser': dummy_parser,
            'methods': methods,
            'has_idrawable_loadstate': has_idrawable_loadstate,
            'has_idrawable_savestate': has_idrawable_savestate
        }
    # Add IDrawableModule parser explicitly if not present
    if 'IDrawableModule' not in registry:
        registry['IDrawableModule'] = {
            'source': None,
            'parser': parse_idrawablemodule_state,
            'methods': {},
            'has_idrawable_loadstate': True,
            'has_idrawable_savestate': True
        }
    return registry


def parse_module_state_with_registry(module_type: str, data: bytes, registry: dict) -> dict:
    """
    Layered parse: If module_type has IDrawableModule::LoadState, parse with IDrawableModule first,
    then with module-specific parser if available (and not dummy_parser).

    Args:
        module_type (str): Module type string.
        data (bytes): Binary module state data.
        registry (dict): Registry mapping module names to parser info.

    Returns:
        dict: Parsed binary module state.
    """
    """
    Layered parse: If module_type has IDrawableModule::LoadState, parse with IDrawableModule first,
    then with module-specific parser if available (and not dummy_parser).
    """
    result = {}
    # Detect if module_type is in registry
    entry = registry.get(module_type)
    if not entry:
        return {'error': f'Module type {module_type} not found in registry', 'raw_bytes': data.hex()}
    # If IDrawableModule::LoadState present, parse with IDrawableModule first
    if entry.get('has_idrawable_loadstate', False):
        base_result = parse_idrawablemodule_state(data)
        result.update(base_result)
        # If remaining bytes and module-specific parser exists, use it
        remaining_bytes = bytes.fromhex(base_result.get('remaining', '')) if 'remaining' in base_result else b''
        module_parser = entry.get('parser', dummy_parser)
        # Only call module-specific parser if it's not dummy and not IDrawableModule
        if module_parser not in (dummy_parser, parse_idrawablemodule_state) and remaining_bytes:
            try:
                module_result = module_parser(remaining_bytes)
                result['module_specific'] = module_result
            except Exception as e:
                result['module_specific_error'] = str(e)
    else:
        # Only module-specific parser
        module_parser = entry.get('parser', dummy_parser)
        try:
            module_result = module_parser(data)
            result.update(module_result)
        except Exception as e:
            result['error'] = str(e)
            result['raw_bytes'] = data.hex()
    return result
    return registry


class TestFindBinaryModules(unittest.TestCase):
    def test_registry_build(self):
        source_dir = '/workspaces/build_bespoke/BespokeSynth/Source'
        registry = build_parser_registry(source_dir)
        self.assertIsInstance(registry, dict)
        self.assertGreater(len(registry), 0, "No modules with binary parsing logic found.")
        for mod, entry in registry.items():
            self.assertIn('source', entry)
            self.assertIn('parser', entry)
            log.info(f"Module: {mod}, Source: {entry['source']}")
            for name, source in entry['methods'].items():
                print(f'///// {mod}::{name}\n', source)


def main():
    import sys
    # Remove extra args after --test before parsing
    argv = sys.argv[:]
    test_args = []
    if '--test' in argv:
        idx = argv.index('--test')
        test_args = argv[idx+1:]
        argv = argv[:idx+1]
        log.debug(f'{sys.argv=}\n{argv=}\n{test_args=}')

    parser = argparse.ArgumentParser(description='Parse or write a .bsk BespokeSynth file.')
    parser.add_argument('bsk_file', nargs='?', help='Path to the .bsk file to parse or write')
    parser.add_argument('--write', metavar='JSON_FILE', help='Write .bsk from JSON file (instead of parsing)')
    parser.add_argument('--32bit', action='store_true', help='Use 32-bit length prefix for writing')
    parser.add_argument('--test', action='store_true', help='Run round-trip tests on .bsk files')
    parser.add_argument('--parse-binary', nargs='?', const='', metavar='MODULE_TYPE', help='Parse binary module state using registry for given module type (optional)')
    args = parser.parse_args(argv[1:])

    if args.test:
        # Pass any extra args after --test to unittest.main
        unittest.main(argv=[''] + test_args, exit=False)
    elif args.write:
        with open(args.write, 'r', encoding='utf-8') as jf:
            data = json.load(jf)
        write_bsk_file(args.bsk_file, data, use_32bit=args.__dict__['32bit'])
    elif args.bsk_file:
        result = parse_bsk_file(args.bsk_file)
        print('--- Parsed JSON ---')
        print(json.dumps(result['json'], indent=2))
        if args.parse_binary is not None:
            # Parse binary module state
            module_type = args.parse_binary
            module_state_hex = result.get('module_state')
            if not module_state_hex:
                print('No binary module state found.')
                return
            module_state_bytes = bytes.fromhex(module_state_hex)
            source_dir = str(Path(__file__).parent.parent / 'Source')
            registry = build_parser_registry(source_dir)
            # If no module_type provided, try to infer from JSON
            if not module_type:
                # Try common keys for module type
                json_data = result.get('json', {})
                module_type = json_data.get('moduleType') or json_data.get('type') or json_data.get('module_type')
                if not module_type:
                    print(('json_data', json_data))
                    print('No module type provided and could not infer from JSON. Please specify MODULE_TYPE.')
                    return
            parsed_binary = parse_module_state_with_registry(module_type, module_state_bytes, registry)
            print('--- Parsed Binary Module State ---')
            print(json.dumps(parsed_binary, indent=2))
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
