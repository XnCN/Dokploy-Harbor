import {readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {fileURLToPath,pathToFileURL} from 'node:url';
import path from 'node:path';
import {render} from './render-compose.mjs';
const root=fileURLToPath(new URL('../',import.meta.url));
const hash=bytes=>'sha256:'+createHash('sha256').update(bytes).digest('hex');
const accept='application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.list.v2+json,application/vnd.docker.distribution.manifest.v2+json';
async function get(url,headers={}) {
  const response=await fetch(url,{headers});
  if(!response.ok) throw new Error('Failed to fetch '+new URL(url).hostname+' (HTTP '+response.status+')');
  return response;
}
export async function inspectImage(repository,tag) {
  const token=await get('https://auth.docker.io/token?service=registry.docker.io&scope=repository:'+repository+':pull').then(r=>r.json());
  const headers={Authorization:'Bearer '+token.token,Accept:accept};
  const base='https://registry-1.docker.io/v2/'+repository;
  const response=await get(base+'/manifests/'+tag,headers);
  const bytes=Buffer.from(await response.arrayBuffer());
  const digest=response.headers.get('docker-content-digest');
  if(hash(bytes)!==digest) throw new Error('Manifest digest mismatch: '+repository);
  let manifest=JSON.parse(bytes);
  if(manifest.manifests) {
    const target=manifest.manifests.find(item=>item.platform?.os==='linux'&&item.platform?.architecture==='amd64');
    if(!target) throw new Error('No linux/amd64 image: '+repository);
    const data=Buffer.from(await get(base+'/manifests/'+target.digest,headers).then(r=>r.arrayBuffer()));
    if(hash(data)!==target.digest) throw new Error('Platform digest mismatch');
    manifest=JSON.parse(data);
  }
  const configBytes=Buffer.from(await get(base+'/blobs/'+manifest.config.digest,headers).then(r=>r.arrayBuffer()));
  if(hash(configBytes)!==manifest.config.digest) throw new Error('Config digest mismatch');
  const metadata=JSON.parse(configBytes);
  if(metadata.os!=='linux'||metadata.architecture!=='amd64') throw new Error('Unsupported image platform');
  const config=metadata.config;
  return {repository,tag,digest,entrypoint:config.Entrypoint||[],command:config.Cmd||[],user:config.User||'root'};
}
export async function latestVersion() {
  const release=await get('https://api.github.com/repos/goharbor/harbor/releases/latest',{Accept:'application/vnd.github+json'}).then(r=>r.json());
  if(release.prerelease||release.draft||!/^v\d+\.\d+\.\d+$/.test(release.tag_name)) throw new Error('Expected a stable Harbor release');
  return release.tag_name;
}
export async function update({checkOnly=false,version}={}) {
  const current=JSON.parse(await readFile(path.join(root,'images.lock.json'),'utf8'));
  const selected=version||await latestVersion();
  if(!/^v\d+\.\d+\.\d+$/.test(selected)) throw new Error('Use an exact stable version such as v2.15.4');
  const refreshed={};
  // Sequential requests avoid Docker Hub anonymous pull bursts.
  for(const [key,image] of Object.entries(current.images)) {
    const tag=image.repository.startsWith('goharbor/')?selected:image.tag;
    refreshed[key]=await inspectImage(image.repository,tag);
  }
  const template=Buffer.from(await get('https://raw.githubusercontent.com/goharbor/harbor/'+selected+'/make/harbor.yml.tmpl').then(r=>r.arrayBuffer()));
  const digest=createHash('sha256').update(template).digest('hex');
  if(checkOnly) {
    if(current.harbor_version!==selected) throw new Error('New stable Harbor available: '+selected+' (locked '+current.harbor_version+')');
    if(JSON.stringify(current.images)!==JSON.stringify(refreshed)||current.template_sha256!==digest) throw new Error('Upstream image metadata or template changed; review before updating');
    return 'Latest stable version and every locked upstream digest/startup command verified: '+selected;
  }
  const next={harbor_version:selected,platform:'linux/amd64',verified_at:new Date().toISOString().slice(0,10),images:refreshed,template_sha256:digest};
  await writeFile(path.join(root,'vendor/harbor.yml.tmpl'),template);
  await writeFile(path.join(root,'images.lock.json'),JSON.stringify(next,null,2)+'\n');
  await render();
  return 'Updated lock, template and Compose to '+selected+'. Review diffs, run tests and use maintenance before production upgrade.';
}
if(typeof process!=='undefined'&&process.argv[1]&&pathToFileURL(path.resolve(process.argv[1])).href===import.meta.url) {
  const args=process.argv.slice(2);
  const at=args.indexOf('--version');
  console.log(await update({checkOnly:args.includes('--check'),version:at<0?undefined:args[at+1]}));
}
