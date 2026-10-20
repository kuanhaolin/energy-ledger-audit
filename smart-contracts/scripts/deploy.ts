import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

import { network } from "hardhat";

const CONTRACT_NAME = "EnergyLedgerAnchor";
const SEPOLIA_CHAIN_ID = 11155111n;

async function main() {
  const { ethers, networkName } = await network.connect();
  const [deployer] = await ethers.getSigners();
  const deployerAddress = await deployer.getAddress();
  const providerNetwork = await ethers.provider.getNetwork();
  const balance = await ethers.provider.getBalance(deployerAddress);

  if (networkName === "sepolia" && providerNetwork.chainId !== SEPOLIA_CHAIN_ID) {
    throw new Error(
      `RPC chainId 錯誤：預期 ${SEPOLIA_CHAIN_ID}，實際 ${providerNetwork.chainId}`,
    );
  }

  console.log(`Network: ${networkName} (chainId=${providerNetwork.chainId})`);
  console.log(`Deployer: ${deployerAddress}`);
  console.log(`Balance: ${ethers.formatEther(balance)} ETH`);

  const contract = await ethers.deployContract(CONTRACT_NAME);
  const deploymentTransaction = contract.deploymentTransaction();

  if (deploymentTransaction === null) {
    throw new Error("找不到部署交易");
  }

  console.log(`Transaction submitted: ${deploymentTransaction.hash}`);
  await contract.waitForDeployment();

  const receipt = await deploymentTransaction.wait();
  if (receipt === null || receipt.status !== 1) {
    throw new Error("合約部署交易失敗");
  }

  const contractAddress = await contract.getAddress();
  const admin = await contract.admin();
  const deployerIsSubmitter = await contract.allowedSubmitters(deployerAddress);

  if (admin.toLowerCase() !== deployerAddress.toLowerCase()) {
    throw new Error("部署後 admin 不是部署錢包");
  }
  if (!deployerIsSubmitter) {
    throw new Error("部署錢包沒有 anchor() 呼叫權限");
  }

  const projectRoot = process.cwd();
  const artifactPath = path.join(
    projectRoot,
    "artifacts",
    "contracts",
    "EnergyLedgerAnchor.sol",
    `${CONTRACT_NAME}.json`,
  );
  const artifact = JSON.parse(await readFile(artifactPath, "utf8")) as {
    abi: unknown;
  };

  const backendArtifactPath = path.resolve(
    projectRoot,
    "../backend/app/contracts/EnergyLedgerAnchor.json",
  );
  await mkdir(path.dirname(backendArtifactPath), { recursive: true });
  await writeFile(
    backendArtifactPath,
    `${JSON.stringify({ abi: artifact.abi }, null, 2)}\n`,
    "utf8",
  );

  const frontendArtifactPath = path.resolve(
    projectRoot,
    "../frontend/web/src/contracts/EnergyLedgerAnchor.json",
  );
  await mkdir(path.dirname(frontendArtifactPath), { recursive: true });
  await writeFile(
    frontendArtifactPath,
    `${JSON.stringify({ abi: artifact.abi }, null, 2)}\n`,
    "utf8",
  );

  const deployment = {
    contractName: CONTRACT_NAME,
    network: networkName,
    chainId: providerNetwork.chainId.toString(),
    address: contractAddress,
    deployer: deployerAddress,
    transactionHash: deploymentTransaction.hash,
    blockNumber: receipt.blockNumber,
    deployedAt: new Date().toISOString(),
  };
  const deploymentsDirectory = path.join(projectRoot, "deployments");
  const deploymentPath = path.join(deploymentsDirectory, `${networkName}.json`);
  await mkdir(deploymentsDirectory, { recursive: true });
  await writeFile(
    deploymentPath,
    `${JSON.stringify(deployment, null, 2)}\n`,
    "utf8",
  );

  console.log("\nDeployment successful");
  console.log(`Contract address: ${contractAddress}`);
  console.log(`Transaction hash: ${deploymentTransaction.hash}`);
  console.log(`Block number: ${receipt.blockNumber}`);
  console.log(`Deployment record: ${deploymentPath}`);
  console.log(`Backend ABI: ${backendArtifactPath}`);
  console.log(`Frontend ABI: ${frontendArtifactPath}`);

  if (networkName === "sepolia") {
    console.log(`Etherscan: https://sepolia.etherscan.io/address/${contractAddress}`);
  }
}

main().catch((error: unknown) => {
  console.error(error);
  process.exitCode = 1;
});
