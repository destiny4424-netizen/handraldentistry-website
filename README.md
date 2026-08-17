# Handral Dentistry Website

## Files
- `index.html` — Public patient website (handraldentistry.com)
- `clinic/` — Staff clinic management app (handraldentistry.com/clinic)

## Deploy to GitHub Pages

1. Create repo on GitHub named `handraldentistry-website`
2. Upload all files
3. Settings → Pages → Source: main branch / root
4. Add custom domain: handraldentistry.com

## DNS Setup (at your domain registrar)
Add these DNS records:
```
Type: A      Name: @    Value: 185.199.108.153
Type: A      Name: @    Value: 185.199.109.153
Type: A      Name: @    Value: 185.199.110.153
Type: A      Name: @    Value: 185.199.111.153
Type: CNAME  Name: www  Value: YOUR-USERNAME.github.io
```

## Before Going Live - Update These
1. Replace all `+91 94480 XXXXX` with real phone numbers
2. Replace `https://wa.me/919448000000` with real WhatsApp number
3. Add real clinic addresses if needed
4. Add real photos to replace placeholders
