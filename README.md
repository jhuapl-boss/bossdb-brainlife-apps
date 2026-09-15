# Contactome Calculation

Compute a contactome on a dataset, based on the code at https://github.com/aplbrain/cloudome. This method was used in [Matelsky et al. 2026](https://doi.org/10.64898/2026.05.08.723866).

Input a segmentation layer where objects touch each other. Output is a SQLite db with table name contactome_edges and columns graph_id, location, pre, post, weight. Weight is measured in units of square nanometers.