"""Read reference inputs in place; produce small, archivable inspection reports."""
import json
import html
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit


def sample_selection(shape, limit):
    """A rectangular sample with at most limit elements, including for 5D data."""
    remaining = limit
    selection = []
    for size in shape:
        count = min(size, remaining)
        selection.append(slice(0, count))
        remaining = max(1, remaining // max(1, count))
    return tuple(selection)


def validate_config(config):
    if config.get('kind') not in ('nwb', 'ome-zarr'):
        raise ValueError('kind must be nwb or ome-zarr')
    uri = config.get('input')
    if not isinstance(uri, str) or not uri:
        raise ValueError('input is required')
    if urlsplit(uri).scheme not in ('', 'https', 'http', 's3'):
        raise ValueError('input must be a local path, HTTPS URL or S3 URI')
    limit = config.get('sample_elements', 64)
    if type(limit) is not int or not 1 <= limit <= 4096:
        raise ValueError('sample_elements must be an integer from 1 to 4096')
    if type(config.get('anonymous', False)) is not bool:
        raise ValueError('anonymous must be a boolean')
    return uri, limit


def summarize(array, limit):
    import numpy as np
    if array.dtype.kind not in 'biuf':
        raise ValueError('Select a numeric array for sampling')
    values = np.asarray(array[sample_selection(array.shape, limit)])
    finite = values[np.isfinite(values)]
    return {'shape': list(array.shape), 'dtype': str(array.dtype),
            'sample_elements': int(values.size), 'finite_elements': int(finite.size),
            'sample_min': float(finite.min()) if finite.size else None,
            'sample_max': float(finite.max()) if finite.size else None}


def inspect_nwb(uri, config, limit):
    import fsspec
    import h5py
    from pynwb import NWBHDF5IO
    options = {'anon': config.get('anonymous', False)} if uri.startswith('s3://') else {}
    # Block caching keeps range reads bounded in memory; no whole-file download.
    with fsspec.open(uri, 'rb', block_size=1024 * 1024, cache_type='blockcache', **options) as remote:
        with h5py.File(remote, 'r') as handle:
            with NWBHDF5IO(file=handle, mode='r', load_namespaces=True) as io:
                nwb = io.read()
                path = config.get('array_path') or 'acquisition'
                if path == 'acquisition':
                    arrays = [item.data for item in nwb.acquisition.values()
                              if hasattr(item, 'data') and hasattr(item.data, 'shape')]
                    if not arrays:
                        raise ValueError('No acquisition TimeSeries: specify an HDF5 array_path')
                    array = arrays[0]
                else:
                    array = handle[path]
                return {'kind': 'nwb', 'nwb_version': str(handle.attrs.get('nwb_version', '')),
                        'acquisition_count': len(nwb.acquisition), **summarize(array, limit)}


def inspect_zarr(uri, config, limit):
    import zarr
    options = {'anon': config.get('anonymous', False)} if uri.startswith('s3://') else {}
    if urlsplit(uri).scheme:
        store = zarr.storage.FsspecStore.from_url(uri, read_only=True, storage_options=options)
    else:
        store = zarr.storage.LocalStore(uri, read_only=True)
    try:
        root = zarr.open_group(store=store, mode='r', use_consolidated=False)
        attrs = dict(root.attrs)
        ome = attrs.get('ome', attrs)
        scales = ome.get('multiscales')
        if not isinstance(scales, list) or not scales:
            raise ValueError('Store has no OME multiscales metadata')
        datasets = scales[0].get('datasets', [])
        path = config.get('array_path') or (datasets[-1]['path'] if datasets else None)
        if not path or path.startswith('/') or '..' in PurePosixPath(path).parts:
            raise ValueError('OME array_path must be a relative child path')
        array = root[path]
        return {'kind': 'ome-zarr', 'array_path': path, 'levels': len(datasets),
                'axes': scales[0].get('axes', []), **summarize(array, limit)}
    finally:
        store.close()


def run(config):
    uri, limit = validate_config(config)
    report = (inspect_nwb if config['kind'] == 'nwb' else inspect_zarr)(uri, config, limit)
    # Never put signed URLs, credentials, or provider metadata into logs/products.
    report['access_scheme'] = urlsplit(uri).scheme or 'file'
    output = Path('report')
    output.mkdir(exist_ok=True)
    (output / 'report.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    (output / 'html').mkdir(exist_ok=True)
    (output / 'html' / 'report.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    title = f"{report['kind'].upper()} reference inspection"
    (output / 'index.html').write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{html.escape(title)}</title>'
        '<style>body{font:16px system-ui;max-width:800px;margin:48px auto;padding:0 24px;'
        'color:#172b3a}pre{background:#f1f5f8;padding:24px;overflow:auto;border-radius:8px}'
        'a{color:#075a96}</style>'
        f'<h1>{html.escape(title)}</h1><p>Source read successfully. '
        f"Sampled {report['sample_elements']} elements.</p>"
        '<p><a href="html/report.json" download>Download JSON report</a></p>'
        f'<pre>{html.escape(json.dumps(report, indent=2, allow_nan=False))}</pre></html>'
    )
    Path('product.json').write_text(json.dumps({'brainlife': [{'type': 'success',
        'msg': f"{report['kind']}: read {report['sample_elements']} sample elements successfully"}]}))
    return report


if __name__ == '__main__':
    try:
        run(json.loads(Path('config.json').read_text()))
    except Exception as error:
        # Backend exceptions may contain presigned URLs or temporary credentials.
        print(f'Reference inspection failed ({type(error).__name__}). Check input access, array path, and format.')
        raise SystemExit(1)
