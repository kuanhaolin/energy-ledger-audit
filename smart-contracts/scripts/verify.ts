import { readFile } from "node:fs/promises";
import path from "node:path";

import { verifyContract } from "@nomicfoundation/hardhat-verify/verify";
import { isAddress } from "ethers";
import hre from "hardhat";

const CONTRACT_NAME = "EnergyLedgerAnchor";
const CONTRACT_FULL_NAME = `contracts/EnergyLedgerAnchor.sol:${CONTRACT_NAME}`;
const SEPOLIA_CHAIN_ID = "11155111";

interface DeploymentRecord {
  address: string;
  chainId: string;
  network: string;
}

async function readDeploymentRecord(): Promise<DeploymentRecord> {
  const deploymentPath = path.join(
    process.cwd(),
    "deployments",
    "sepolia.json",
  );

  try {
    return JSON.parse(
      await readFile(deploymentPath, "utf8"),
    ) as DeploymentRecord;
  } catch (error) {
    throw new Error(
      `讀不到 ${deploymentPath}。請先執行 npm run deploy:sepolia，` +
        "或在 .env 設定 VERIFY_CONTRACT_ADDRESS。",
      { cause: error },
    );
  }
}

async function main() {
  const addressFromEnvironment = process.env.VERIFY_CONTRACT_ADDRESS?.trim();
  let contractAddress = addressFromEnvironment;

  if (!contractAddress) {
    const deployment = await readDeploymentRecord();

    if (
      deployment.network !== "sepolia" ||
      deployment.chainId !== SEPOLIA_CHAIN_ID
    ) {
      throw new Error(
        "deployments/sepolia.json 不是 Ethereum Sepolia（chainId 11155111）的部署紀錄",
      );
    }

    contractAddress = deployment.address;
  }

  if (!isAddress(contractAddress)) {
    throw new Error(`合約地址格式錯誤：${contractAddress}`);
  }

  console.log(`Contract: ${CONTRACT_FULL_NAME}`);
  console.log(`Address: ${contractAddress}`);
  console.log("Constructor arguments: []");
  console.log("Submitting source code to Etherscan...");

  const verified = await verifyContract(
    {
      address: contractAddress,
      constructorArgs: [],
      contract: CONTRACT_FULL_NAME,
      provider: "etherscan",
    },
    hre,
  );

  if (!verified) {
    throw new Error("Etherscan 未確認驗證成功");
  }

  console.log("\nSource code verification successful");
  console.log(
    `Etherscan: https://sepolia.etherscan.io/address/${contractAddress}#code`,
  );
}

main().catch((error: unknown) => {
  console.error(error);
  process.exitCode = 1;
});
