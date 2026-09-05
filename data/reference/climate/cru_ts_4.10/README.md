# CRU-TS v4.10 annual climate context

"
"Climate Pulse stores a compact annual derivative of the current CRU-TS release for fast event-location climate context. The authoritative monthly CRU files remain external and are not copied into this repository.

"
"## Coverage

"
"- Source: **CRU-TS v4.10**, Climatic Research Unit, University of East Anglia
"
"- Period: **1901-2025**
"
"- Grid: **0.5° × 0.5°**, global land except Antarctica
"
"- One compressed NetCDF file per year

"
"## Variables

"
"- `tmp` — annual mean temperature (°C), day-weighted from monthly means
"
"- `pre` — annual precipitation total (mm/year), sum of monthly totals
"
"- `vap` — annual mean actual vapour pressure (hPa), day-weighted from monthly means
"
"- `vpd` — annual mean vapour pressure deficit (hPa), calculated at monthly resolution first and then day-weighted to annual mean

"
"## Monthly VPD calculation

"
"`VPD = SVP - AVP`, with `AVP = CRU vap`. Saturation vapour pressure is:

"
"`SVP = 6.1078 × exp(aT/(T+b))` hPa

"
"where `(a,b)=(17.269,237.3)` for `T >= 0°C` and `(21.875,265.5)` for `T < 0°C`.

"
"**Temporal-resolution caveat:** this uses monthly-mean temperature. Because SVP is nonlinear in temperature, it cannot reproduce sub-daily temperature variability or asymmetric daytime/nighttime warming. It is intended for long-term local climate context, not sub-daily VPD diagnosis. See [Zhong et al. (2025), Nature Communications 16, 8247](https://doi.org/10.1038/s41467-025-63672-z).

"
"## References

"
"- [CRU-TS v4.10 source](https://crudata.uea.ac.uk/cru/data/hrg/cru_ts_4.10/)
"
"- [Harris et al. (2020), Scientific Data 7, 109](https://doi.org/10.1038/s41597-020-0453-3)
"
"- [Zhong et al. (2025), Nature Communications 16, 8247](https://doi.org/10.1038/s41467-025-63672-z)

"
"## Licence

"
"CRU-TS is made available under the Open Database License, with individual contents under the Database Contents License, under Attribution and Share-Alike conditions. Attribution: **Climatic Research Unit, University of East Anglia**.
"
